from __future__ import annotations

from dataclasses import dataclass, field
import statistics
from typing import Mapping, Sequence

from .event_study import EventStudyConfig, RollingMedianAbsReturn, classify_response_vol_normalized
from .metrics import RollingNormalizer


@dataclass(slots=True)
class _ResponseState:
    flow_norm: RollingNormalizer
    impact_scale: RollingMedianAbsReturn


@dataclass(slots=True)
class OnlineResponseTracker:
    """Causal online equivalent of the response-family event-study trigger.

    A completed window is scored against prior completed windows, then appended to
    the reference history. The current window therefore never helps define its own
    extremeness or volatility scale.
    """

    config: EventStudyConfig = field(default_factory=EventStudyConfig)
    bucket_ms: int = 30_000
    _states: dict[tuple[str, str], _ResponseState] = field(default_factory=dict)
    _families: dict[tuple[str, str, str], tuple[int, int, int]] = field(default_factory=dict)
    _next_family_id: int = 1

    def _state(self, asset: str, market: str) -> _ResponseState:
        key = (asset, market)
        state = self._states.get(key)
        if state is None:
            state = _ResponseState(
                RollingNormalizer(self.config.history_maxlen, self.config.min_history),
                RollingMedianAbsReturn(self.config.history_maxlen, self.config.min_history),
            )
            self._states[key] = state
        return state

    def observe(self, row: Mapping, *, reference_return_bps: float,
                reference_close: float) -> dict:
        asset = str(row["asset"])
        market = str(row["market"])
        start_ms = int(row["start_ms"])
        state = self._state(asset, market)
        delta = float(row.get("delta_usd", 0.0))
        prior_scale = state.impact_scale.scale_then_update(float(reference_return_bps))
        score = state.flow_norm.score_then_update(abs(delta))
        response_class, units = classify_response_vol_normalized(
            delta,
            float(reference_return_bps),
            prior_scale,
            self.config.vol_absorption_units,
            self.config.vol_continuation_units,
            self.config.min_impact_scale_bps,
        )

        observation = {
            "asset": asset,
            "market": market,
            "start_ms": start_ms,
            "end_ms": int(row["end_ms"]),
            "delta_usd": delta,
            "gross_usd": float(row.get("gross_usd", 0.0)),
            "flow_ratio": float(row.get("flow_ratio", 0.0)),
            "venue_count": int(row.get("venue_count", 0)),
            "reference_return_bps": float(reference_return_bps),
            "reference_close": float(reference_close),
            "abs_delta_percentile_past_only": score.percentile,
            "abs_delta_zscore_past_only": score.zscore,
            "impact_scale_bps_past_only": prior_scale,
            "signed_impact_units_past_only": units,
            "response_class": response_class,
            "flow_basis": "raw_usd_sum",
            "price_basis": "median_active_venue_return",
            "causal_trigger": True,
        }

        is_extreme = (
            score.percentile is not None
            and score.percentile >= self.config.extreme_percentile
        )
        if not is_extreme:
            observation["response_event"] = None
            return observation

        event = dict(observation)
        event.pop("response_event", None)
        side = "buy" if delta > 0 else "sell" if delta < 0 else "flat"
        bucket_index = start_ms // self.bucket_ms
        family_key = (asset, market, side)
        prev = self._families.get(family_key)
        if prev is not None and bucket_index - prev[0] <= self.config.family_cooldown_buckets:
            family_id = prev[1]
            family_rank = prev[2] + 1
            independent = False
        else:
            family_id = self._next_family_id
            self._next_family_id += 1
            family_rank = 0
            independent = True
        self._families[family_key] = (bucket_index, family_id, family_rank)
        event.update({
            "event_family_id": family_id,
            "event_family_rank": family_rank,
            "is_independent_event": independent,
            "flow_side": side,
        })
        observation["response_event"] = event
        return observation


def median_reference(buckets: Sequence) -> tuple[float, float]:
    """Equal-venue price reference: median return and median close price."""
    if not buckets:
        raise ValueError("at least one bucket is required")
    returns = [float(b.return_bps) for b in buckets]
    closes = [float(b.close_price) for b in buckets]
    return statistics.median(returns), statistics.median(closes)

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import statistics
from typing import Iterable, Mapping, Sequence

from .metrics import RollingNormalizer


@dataclass(frozen=True, slots=True)
class EventStudyConfig:
    extreme_percentile: float = 0.99
    max_absorption_response_bps: float = 3.0
    min_continuation_response_bps: float = 5.0
    min_history: int = 200
    history_maxlen: int = 20000
    horizons_buckets: tuple[int, ...] = (1, 2, 4, 10, 30, 60, 120)
    family_cooldown_buckets: int = 4
    vol_absorption_units: float = 0.5
    vol_continuation_units: float = 1.0
    min_impact_scale_bps: float = 0.1


class RollingMedianAbsReturn:
    """Past-only robust scale for the current bucket's price response."""

    def __init__(self, maxlen: int = 20000, min_history: int = 200) -> None:
        self.values: deque[float] = deque(maxlen=maxlen)
        self.min_history = min_history

    def scale_then_update(self, return_bps: float) -> float | None:
        scale = statistics.median(self.values) if len(self.values) >= self.min_history else None
        self.values.append(abs(float(return_bps)))
        return scale


def classify_response_legacy(delta_usd: float, return_bps: float,
                             max_absorption_response_bps: float = 3.0,
                             min_continuation_response_bps: float = 5.0) -> str:
    """Original pilot classifier retained verbatim for audit/comparison."""
    if delta_usd == 0:
        return "neutral_flow"
    signed_response = return_bps * (1.0 if delta_usd > 0 else -1.0)
    if signed_response <= max_absorption_response_bps:
        return "absorption_candidate"
    if signed_response >= min_continuation_response_bps:
        return "continuation_candidate"
    return "intermediate"


def classify_response(delta_usd: float, return_bps: float,
                      max_absorption_response_bps: float = 3.0,
                      min_continuation_response_bps: float = 5.0) -> str:
    """Corrected fixed-bps classifier.

    Absorption means weak response in either direction around zero. A sufficiently
    large move against the aggressive-flow direction is a separate instant reversal,
    not absorption.
    """
    if delta_usd == 0:
        return "neutral_flow"
    signed_response = return_bps * (1.0 if delta_usd > 0 else -1.0)
    if signed_response < -max_absorption_response_bps:
        return "instant_reversal"
    if abs(signed_response) <= max_absorption_response_bps:
        return "absorption_candidate"
    if signed_response >= min_continuation_response_bps:
        return "continuation_candidate"
    return "intermediate"


def classify_response_vol_normalized(delta_usd: float, return_bps: float,
                                     impact_scale_bps: float | None,
                                     absorption_units: float = 0.5,
                                     continuation_units: float = 1.0,
                                     min_scale_bps: float = 0.1) -> tuple[str, float | None]:
    """Classify impact relative to a robust scale computed from PRIOR buckets only.

    Thresholds are preregistered methodology defaults, not fitted to the pilot:
    <= -0.5 units instant reversal; |impact| <= 0.5 absorption; >= 1.0 continuation.
    """
    if delta_usd == 0:
        return "neutral_flow", 0.0
    if impact_scale_bps is None:
        return "insufficient_vol_history", None
    scale = max(float(impact_scale_bps), min_scale_bps)
    signed_response = return_bps * (1.0 if delta_usd > 0 else -1.0)
    units = signed_response / scale
    if units < -absorption_units:
        return "instant_reversal", units
    if abs(units) <= absorption_units:
        return "absorption_candidate", units
    if units >= continuation_units:
        return "continuation_candidate", units
    return "intermediate", units


def assign_event_families(events: Sequence[Mapping], cooldown_buckets: int = 4) -> list[dict]:
    """Causally cluster adjacent extreme windows and mark only the first as independent.

    The representative is always the first observed event in a family. Selecting the
    later strongest event would introduce look-ahead into an online signal definition.
    """
    if cooldown_buckets < 0:
        raise ValueError("cooldown_buckets must be >= 0")
    out: list[dict] = []
    state: dict[tuple[str, str, str], tuple[int, int, int]] = {}
    next_family = 1
    for raw in sorted(events, key=lambda e: int(e.get("_bucket_index", 0))):
        e = dict(raw)
        idx = int(e.get("_bucket_index", 0))
        side = "buy" if float(e.get("delta_usd", 0.0)) > 0 else "sell" if float(e.get("delta_usd", 0.0)) < 0 else "flat"
        key = (str(e.get("asset", "")), str(e.get("market", "")), side)
        prev = state.get(key)
        if prev is not None and idx - prev[0] <= cooldown_buckets:
            family_id, family_rank = prev[1], prev[2] + 1
            independent = False
        else:
            family_id, family_rank = next_family, 0
            next_family += 1
            independent = True
        state[key] = (idx, family_id, family_rank)
        e["event_family_id"] = family_id
        e["event_family_rank"] = family_rank
        e["is_independent_event"] = independent
        e.pop("_bucket_index", None)
        out.append(e)
    return out


def independent_events(events: Iterable[Mapping]) -> list[dict]:
    return [dict(e) for e in events if bool(e.get("is_independent_event", True))]


def build_event_table(composite_rows: Sequence[Mapping], reference_buckets: Sequence[Mapping],
                      config: EventStudyConfig | None = None) -> list[dict]:
    """Build causal extreme-flow events, response classes and future-return labels.

    Flow extremeness and impact scale are computed from PRIOR observations only.
    Legacy response classification is retained as a separate field for reproducibility.
    """
    cfg = config or EventStudyConfig()
    ref = {int(r["start_ms"]): r for r in reference_buckets}
    ordered = sorted(composite_rows, key=lambda r: int(r["start_ms"]))
    flow_norm = RollingNormalizer(maxlen=cfg.history_maxlen, min_history=cfg.min_history)
    impact_scale = RollingMedianAbsReturn(maxlen=cfg.history_maxlen, min_history=cfg.min_history)
    out: list[dict] = []

    for i, row in enumerate(ordered):
        start = int(row["start_ms"])
        price_row = ref.get(start)
        delta = float(row["delta_usd"])
        if not price_row:
            flow_norm.score_then_update(abs(delta))
            continue

        current_return_bps = float(price_row.get("return_bps", 0.0))
        prior_impact_scale = impact_scale.scale_then_update(current_return_bps)
        score = flow_norm.score_then_update(abs(delta))
        if score.percentile is None or score.percentile < cfg.extreme_percentile:
            continue

        current_close = float(price_row["close_price"])
        sign = 1.0 if delta > 0 else -1.0 if delta < 0 else 0.0
        vol_class, signed_impact_units = classify_response_vol_normalized(
            delta, current_return_bps, prior_impact_scale,
            cfg.vol_absorption_units, cfg.vol_continuation_units, cfg.min_impact_scale_bps,
        )
        event = dict(row)
        event.update({
            "_bucket_index": i,
            "reference_close": current_close,
            "reference_return_bps": current_return_bps,
            "abs_delta_percentile_past_only": score.percentile,
            "abs_delta_zscore_past_only": score.zscore,
            "impact_scale_bps_past_only": prior_impact_scale,
            "signed_impact_units_past_only": signed_impact_units,
            "response_class_legacy": classify_response_legacy(
                delta, current_return_bps,
                cfg.max_absorption_response_bps, cfg.min_continuation_response_bps,
            ),
            "response_class_fixed_bps": classify_response(
                delta, current_return_bps,
                cfg.max_absorption_response_bps, cfg.min_continuation_response_bps,
            ),
            "response_class": vol_class,
        })
        for h in cfg.horizons_buckets:
            if i + h >= len(ordered):
                event[f"future_{h}b_raw_bps"] = None
                event[f"future_{h}b_in_flow_direction_bps"] = None
                continue
            future_start = int(ordered[i + h]["start_ms"])
            future_ref = ref.get(future_start)
            if not future_ref:
                event[f"future_{h}b_raw_bps"] = None
                event[f"future_{h}b_in_flow_direction_bps"] = None
                continue
            future_close = float(future_ref["close_price"])
            raw_bps = ((future_close / current_close) - 1.0) * 10000.0 if current_close else 0.0
            event[f"future_{h}b_raw_bps"] = raw_bps
            event[f"future_{h}b_in_flow_direction_bps"] = raw_bps * sign
        out.append(event)
    return assign_event_families(out, cfg.family_cooldown_buckets)


def summarize_events(events: Iterable[Mapping], horizon_buckets: int) -> list[dict]:
    key = f"future_{horizon_buckets}b_raw_bps"
    groups: dict[str, list[float]] = {}
    for e in events:
        value = e.get(key)
        if value is None:
            continue
        groups.setdefault(str(e.get("response_class", "unknown")), []).append(float(value))
    out = []
    for name, vals in sorted(groups.items()):
        vals_sorted = sorted(vals)
        n = len(vals_sorted)
        mean = sum(vals_sorted) / n
        median = vals_sorted[n // 2] if n % 2 else (vals_sorted[n // 2 - 1] + vals_sorted[n // 2]) / 2
        pos_rate = sum(v > 0 for v in vals_sorted) / n
        out.append({"response_class": name, "n": n, "mean_raw_bps": mean,
                    "median_raw_bps": median, "positive_rate": pos_rate})
    return out

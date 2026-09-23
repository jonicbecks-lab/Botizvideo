from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class _Episode:
    side: int
    peak_abs_flow: float
    peak_bucket_index: int
    peak_percentile: float


class CausalExhaustionDetector:
    """Detect flow exhaustion only after the weakening is actually observed.

    A candidate starts with an extreme flow observation. It triggers later when:
      * flow is still on the same side (or flat),
      * absolute flow has decayed to <= `decay_ratio` of the observed episode peak,
      * price impact in the original flow direction is weak (<= `max_impact_units`),
      * the trigger arrives within `max_followup_buckets` of the latest peak.

    The detector never labels the original peak using future information. The signal
    timestamp is the bucket in which decay + weak impact have become observable.
    """

    def __init__(self, extreme_percentile: float = 0.99, decay_ratio: float = 0.5,
                 max_impact_units: float = 0.5, max_followup_buckets: int = 4) -> None:
        if not 0.0 < extreme_percentile <= 1.0:
            raise ValueError("extreme_percentile must be in (0, 1]")
        if not 0.0 < decay_ratio < 1.0:
            raise ValueError("decay_ratio must be in (0, 1)")
        if max_followup_buckets < 1:
            raise ValueError("max_followup_buckets must be >= 1")
        self.extreme_percentile = extreme_percentile
        self.decay_ratio = decay_ratio
        self.max_impact_units = max_impact_units
        self.max_followup_buckets = max_followup_buckets
        self._episodes: dict[tuple[str, str], _Episode] = {}

    def observe(self, asset: str, market: str, bucket_index: int, delta_usd: float,
                abs_delta_percentile_past_only: float | None,
                signed_impact_units_past_only: float | None) -> dict | None:
        key = (asset, market)
        delta = float(delta_usd)
        abs_flow = abs(delta)
        side = 1 if delta > 0 else -1 if delta < 0 else 0
        pct = abs_delta_percentile_past_only
        impact = signed_impact_units_past_only
        episode = self._episodes.get(key)

        if episode is not None and bucket_index - episode.peak_bucket_index > self.max_followup_buckets:
            self._episodes.pop(key, None)
            episode = None

        # A fresh extreme begins an episode. A stronger same-side extreme observed
        # later becomes the new causal peak; this is an online update, not look-ahead.
        if pct is not None and pct >= self.extreme_percentile and side != 0:
            if episode is None or side != episode.side or abs_flow >= episode.peak_abs_flow:
                self._episodes[key] = _Episode(side, abs_flow, bucket_index, float(pct))
                return None

        if episode is None or bucket_index <= episode.peak_bucket_index:
            return None

        # Opposite aggressive flow is a different phenomenon (reversal/side flip),
        # so do not call it same-side exhaustion.
        if side != 0 and side != episode.side:
            self._episodes.pop(key, None)
            return None
        if impact is None:
            return None

        flow_ratio_to_peak = abs_flow / episode.peak_abs_flow if episode.peak_abs_flow else 0.0
        if flow_ratio_to_peak <= self.decay_ratio and float(impact) <= self.max_impact_units:
            self._episodes.pop(key, None)
            return {
                "event_type": "exhaustion_candidate",
                "pattern_family": "exhaustion",
                "pattern_label": "exhaustion_candidate",
                "asset": asset,
                "market": market,
                "trigger_bucket_index": int(bucket_index),
                "peak_bucket_index": episode.peak_bucket_index,
                "flow_side": "buy" if episode.side > 0 else "sell",
                "peak_abs_flow_usd": episode.peak_abs_flow,
                "peak_percentile_past_only": episode.peak_percentile,
                "trigger_abs_flow_usd": abs_flow,
                "flow_ratio_to_peak": flow_ratio_to_peak,
                "trigger_signed_impact_units_past_only": float(impact),
                # Exhaustion is evaluated as a reversal hypothesis, but this is only
                # a label for forward testing, not an assumed trading edge.
                "hypothesis_direction": -episode.side,
                "causal_trigger": True,
            }
        return None

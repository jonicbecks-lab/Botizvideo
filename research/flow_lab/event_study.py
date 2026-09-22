from __future__ import annotations

from dataclasses import dataclass
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


def classify_response(delta_usd: float, return_bps: float,
                      max_absorption_response_bps: float = 3.0,
                      min_continuation_response_bps: float = 5.0) -> str:
    if delta_usd == 0:
        return "neutral_flow"
    signed_response = return_bps * (1.0 if delta_usd > 0 else -1.0)
    if signed_response <= max_absorption_response_bps:
        return "absorption_candidate"
    if signed_response >= min_continuation_response_bps:
        return "continuation_candidate"
    return "intermediate"


def build_event_table(composite_rows: Sequence[Mapping], reference_buckets: Sequence[Mapping],
                      config: EventStudyConfig | None = None) -> list[dict]:
    """Build causal event rows and future-return labels.

    Composite rows are flow-only. Reference buckets provide price. The current flow
    observation is scored against PRIOR absolute deltas before being appended.
    """
    cfg = config or EventStudyConfig()
    ref = {int(r["start_ms"]): r for r in reference_buckets}
    ordered = sorted(composite_rows, key=lambda r: int(r["start_ms"]))
    norm = RollingNormalizer(maxlen=cfg.history_maxlen, min_history=cfg.min_history)
    out: list[dict] = []

    for i, row in enumerate(ordered):
        start = int(row["start_ms"])
        price_row = ref.get(start)
        if not price_row:
            norm.score_then_update(abs(float(row["delta_usd"])))
            continue
        delta = float(row["delta_usd"])
        score = norm.score_then_update(abs(delta))
        if score.percentile is None or score.percentile < cfg.extreme_percentile:
            continue
        current_close = float(price_row["close_price"])
        current_return_bps = float(price_row.get("return_bps", 0.0))
        sign = 1.0 if delta > 0 else -1.0 if delta < 0 else 0.0
        event = dict(row)
        event.update({
            "reference_close": current_close,
            "reference_return_bps": current_return_bps,
            "abs_delta_percentile_past_only": score.percentile,
            "abs_delta_zscore_past_only": score.zscore,
            "response_class": classify_response(delta, current_return_bps,
                                                cfg.max_absorption_response_bps,
                                                cfg.min_continuation_response_bps),
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
    return out


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

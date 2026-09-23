from __future__ import annotations

import statistics
from typing import Iterable, Mapping, Sequence

from .patterns import response_pattern


DEFAULT_HORIZONS = (1, 2, 4, 10, 30, 60, 120)


def _derive_hypothesis_labels(event: dict, horizons_buckets: Sequence[int]) -> dict:
    direction = int(event.get("hypothesis_direction", 0) or 0)
    for h in horizons_buckets:
        raw_key = f"future_{int(h)}b_raw_bps"
        hypo_key = f"future_{int(h)}b_hypothesis_bps"
        raw = event.get(raw_key)
        event[hypo_key] = float(raw) * direction if raw is not None and direction else None
    return event


def build_response_pattern_events(
    events: Iterable[Mapping],
    horizons_buckets: Sequence[int] = DEFAULT_HORIZONS,
) -> list[dict]:
    """Map extreme-flow response classes into the common pattern-event schema.

    Existing future raw-return labels from event_study are converted into the
    pattern's preregistered hypothesis direction. This does not alter trigger-time
    features and is label-only.
    """
    out: list[dict] = []
    for event in events:
        mapped = response_pattern(event)
        if mapped is not None:
            out.append(_derive_hypothesis_labels(mapped, horizons_buckets))
    return out


def attach_future_returns(
    events: Iterable[Mapping],
    reference_buckets: Sequence[Mapping],
    horizons_buckets: Sequence[int] = DEFAULT_HORIZONS,
) -> list[dict]:
    """Attach forward-return labels after a pattern has already triggered.

    `reference_buckets` must be chronological completed buckets. Features in the
    input event are never modified using future data; forward returns are labels.
    """
    refs = sorted(reference_buckets, key=lambda r: int(r["start_ms"]))
    index = {int(r["start_ms"]): i for i, r in enumerate(refs)}
    out: list[dict] = []

    for raw in events:
        event = dict(raw)
        start = event.get("start_ms")
        if start is None or int(start) not in index:
            continue
        i = index[int(start)]
        current_close = float(refs[i]["close_price"])
        direction = int(event.get("hypothesis_direction", 0) or 0)

        for h in horizons_buckets:
            raw_key = f"future_{int(h)}b_raw_bps"
            hypo_key = f"future_{int(h)}b_hypothesis_bps"
            if i + int(h) >= len(refs):
                event[raw_key] = None
                event[hypo_key] = None
                continue
            future_close = float(refs[i + int(h)]["close_price"])
            raw_bps = ((future_close / current_close) - 1.0) * 10000.0 if current_close else 0.0
            event[raw_key] = raw_bps
            event[hypo_key] = raw_bps * direction if direction else None
        out.append(event)
    return out


def summarize_pattern_events(
    events: Iterable[Mapping],
    horizons_buckets: Sequence[int] = DEFAULT_HORIZONS,
) -> list[dict]:
    """Summarize every pattern family/label with the same forward metrics."""
    event_list = [dict(e) for e in events]
    groups: dict[tuple[str, str], list[dict]] = {}
    for event in event_list:
        family = str(event.get("pattern_family", "unknown"))
        label = str(event.get("pattern_label", family))
        groups.setdefault((family, label), []).append(event)

    out: list[dict] = []
    for (family, label), rows in sorted(groups.items()):
        result: dict = {
            "pattern_family": family,
            "pattern_label": label,
            "raw_event_count": len(rows),
            "independent_event_count": sum(bool(r.get("is_independent_event", True)) for r in rows),
        }
        for h in horizons_buckets:
            raw_vals = [
                float(r[f"future_{int(h)}b_raw_bps"])
                for r in rows
                if r.get(f"future_{int(h)}b_raw_bps") is not None
            ]
            hypo_vals = [
                float(r[f"future_{int(h)}b_hypothesis_bps"])
                for r in rows
                if r.get(f"future_{int(h)}b_hypothesis_bps") is not None
            ]
            prefix = f"h{int(h)}"
            result[f"{prefix}_n"] = len(raw_vals)
            result[f"{prefix}_mean_raw_bps"] = statistics.fmean(raw_vals) if raw_vals else None
            result[f"{prefix}_median_raw_bps"] = statistics.median(raw_vals) if raw_vals else None
            result[f"{prefix}_mean_hypothesis_bps"] = statistics.fmean(hypo_vals) if hypo_vals else None
            result[f"{prefix}_median_hypothesis_bps"] = statistics.median(hypo_vals) if hypo_vals else None
            result[f"{prefix}_hypothesis_hit_rate"] = (
                sum(v > 0 for v in hypo_vals) / len(hypo_vals) if hypo_vals else None
            )
        out.append(result)
    return out

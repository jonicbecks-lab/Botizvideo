from __future__ import annotations

from typing import Mapping


def orderbook_absorption_evidence(delta_usd: float, return_bps: float,
                                  impact_scale_bps: float | None,
                                  depth_change: Mapping[str, float | bool],
                                  min_scale_bps: float = 0.1) -> dict:
    """Combine weak price impact and same-side book refill into a diagnostic score.

    This is deliberately an evidence score, not a trading signal. It is meaningful
    only when trade-flow and book-change windows are aligned. Snapshot-based book
    additions remain a proxy until sequence-aware full-book reconstruction is used.

    Score components are preregistered and equally weighted:
      * weak-impact component: 1 at zero impact, linearly falling to 0 at 1 robust
        past-only impact unit;
      * refill component: relevant bid/ask additions divided by aggressive flow,
        capped at 1.
    """
    if delta_usd == 0 or impact_scale_bps is None or not depth_change.get("depth_change_valid"):
        return {"absorption_evidence_valid": False}

    sign = 1.0 if delta_usd > 0 else -1.0
    scale = max(float(impact_scale_bps), min_scale_bps)
    signed_impact_units = float(return_bps) * sign / scale
    magnitude = max(abs(float(delta_usd)), 1.0)

    if delta_usd < 0:
        direction = "bid_absorption"
        refill_ratio = float(depth_change.get("bid_add_usd", 0.0)) / magnitude
    else:
        direction = "ask_absorption"
        refill_ratio = float(depth_change.get("ask_add_usd", 0.0)) / magnitude

    weak_impact_component = max(0.0, 1.0 - min(abs(signed_impact_units), 1.0))
    refill_component = max(0.0, min(refill_ratio, 1.0))
    score = 0.5 * weak_impact_component + 0.5 * refill_component

    return {
        "absorption_evidence_valid": True,
        "absorption_direction": direction,
        "signed_impact_units_past_only": signed_impact_units,
        "relevant_refill_vs_flow": refill_ratio,
        "weak_impact_component": weak_impact_component,
        "refill_component": refill_component,
        "absorption_evidence_score": score,
    }

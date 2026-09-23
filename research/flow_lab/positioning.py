from __future__ import annotations


def classify_oi_positioning(price_return_bps: float, delta_oi_usd: float,
                            delta_flow_usd: float,
                            min_price_bps: float = 0.5,
                            min_oi_usd: float = 1.0,
                            min_flow_usd: float = 1.0) -> dict:
    """Descriptive decomposition of price, OI and aggressive flow.

    The labels are hypotheses about positioning mechanics, not identification of who
    traded or proof of causality. A venue's OI is aggregate and must be time-aligned
    with the same venue's flow/price window before use.
    """
    p = float(price_return_bps)
    oi = float(delta_oi_usd)
    flow = float(delta_flow_usd)

    p_sign = 1 if p >= min_price_bps else -1 if p <= -min_price_bps else 0
    oi_sign = 1 if oi >= min_oi_usd else -1 if oi <= -min_oi_usd else 0
    flow_sign = 1 if flow >= min_flow_usd else -1 if flow <= -min_flow_usd else 0

    if oi_sign == 0:
        label = "flat_oi_or_noise"
    elif oi_sign > 0 and p_sign > 0 and flow_sign > 0:
        label = "new_longs_building"
    elif oi_sign > 0 and p_sign < 0 and flow_sign < 0:
        label = "new_shorts_building"
    elif oi_sign < 0 and p_sign > 0:
        label = "short_unwind_or_liquidation"
    elif oi_sign < 0 and p_sign < 0:
        label = "long_unwind_or_liquidation"
    elif oi_sign > 0 and p_sign == 0:
        label = "position_build_without_price_extension"
    else:
        label = "mixed_positioning"

    alignment = 0
    if p_sign != 0 and flow_sign != 0:
        alignment = 1 if p_sign == flow_sign else -1

    return {
        "pattern_family": "oi_positioning",
        "pattern_label": label,
        "positioning_label": label,
        "price_sign": p_sign,
        "oi_sign": oi_sign,
        "flow_sign": flow_sign,
        "price_flow_alignment": alignment,
        "delta_oi_usd": oi,
        "delta_flow_usd": flow,
        "price_return_bps": p,
        # Positioning labels are descriptive context. Do not assume a predictive
        # direction until the event study establishes one out of sample.
        "hypothesis_direction": 0,
        "causal_trigger": True,
    }

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from .metrics import RollingNormalizer


@dataclass(frozen=True, slots=True)
class PatternThresholds:
    """Frozen research defaults for non-absorption pattern families.

    These values are methodology defaults. They must not be optimized on a period
    after inspecting that period's forward returns.
    """

    min_cross_venue_count: int = 3
    cross_consensus_abs_z: float = 1.0
    cross_consensus_ratio: float = 0.75
    cross_divergence_dispersion: float = 1.25
    cross_opposite_abs_z: float = 1.0

    spot_perp_min_abs_z: float = 0.75
    spot_perp_muted_abs_z: float = 0.25

    liquidation_extreme_percentile: float = 0.99
    liquidation_side_share: float = 0.70
    liquidation_min_price_bps: float = 0.5


_RESPONSE_PATTERN = {
    "absorption_candidate": "absorption",
    "continuation_candidate": "continuation",
    "instant_reversal": "instant_reversal",
}


def response_pattern(event: Mapping) -> dict | None:
    """Convert an extreme-flow response event into a common pattern record."""
    response_class = str(event.get("response_class", ""))
    pattern = _RESPONSE_PATTERN.get(response_class)
    if pattern is None:
        return None

    delta = float(event.get("delta_usd", 0.0))
    flow_side = 1 if delta > 0 else -1 if delta < 0 else 0
    if pattern == "continuation":
        hypothesis_direction = flow_side
    elif pattern in {"absorption", "instant_reversal"}:
        # Research hypothesis only: test whether the subsequent move reverses flow.
        hypothesis_direction = -flow_side
    else:
        hypothesis_direction = 0

    out = dict(event)
    out["pattern_family"] = pattern
    out["pattern_label"] = pattern
    out["flow_side"] = "buy" if flow_side > 0 else "sell" if flow_side < 0 else "flat"
    out["hypothesis_direction"] = hypothesis_direction
    return out


def classify_cross_venue(row: Mapping, thresholds: PatternThresholds | None = None) -> dict:
    """Classify causal same-window cross-venue confirmation/divergence.

    Input is expected to be an EqualVenueComposite row. Only past-normalized venue
    z-scores are used. No exchange receives a discretionary weight. Consensus is
    preregistered as a continuation hypothesis; divergence carries no assumed future
    direction until out-of-sample evidence establishes one.
    """
    cfg = thresholds or PatternThresholds()
    details = [
        d for d in (row.get("venue_details") or [])
        if d.get("zscore_past_only") is not None
    ]
    zscores = [float(d["zscore_past_only"]) for d in details]
    exchanges = [str(d.get("exchange", "")) for d in details]
    avg_z = row.get("equal_venue_zscore")
    avg_z = float(avg_z) if avg_z is not None else None
    dispersion = row.get("venue_zscore_dispersion")
    dispersion = float(dispersion) if dispersion is not None else None
    sign_consensus = float(row.get("sign_consensus", 0.0))

    direction = 0
    if len(zscores) < cfg.min_cross_venue_count:
        label = "insufficient_venues"
    else:
        has_opposition = (
            max(zscores) >= cfg.cross_opposite_abs_z
            and min(zscores) <= -cfg.cross_opposite_abs_z
        )
        high_dispersion = (
            dispersion is not None
            and dispersion >= cfg.cross_divergence_dispersion
            and sign_consensus <= 0.5
        )
        if has_opposition or high_dispersion:
            label = "cross_venue_divergence"
        elif (
            avg_z is not None
            and abs(avg_z) >= cfg.cross_consensus_abs_z
            and sign_consensus >= cfg.cross_consensus_ratio
        ):
            label = "cross_venue_consensus_buy" if avg_z > 0 else "cross_venue_consensus_sell"
            direction = 1 if avg_z > 0 else -1
        else:
            label = "cross_venue_mixed"

    return {
        "pattern_family": "cross_venue",
        "pattern_label": label,
        "asset": row.get("asset"),
        "market": row.get("market"),
        "start_ms": row.get("start_ms"),
        "end_ms": row.get("end_ms"),
        "normalized_venue_count": len(zscores),
        "normalized_active_venues": exchanges,
        "equal_venue_zscore": avg_z,
        "venue_zscore_dispersion": dispersion,
        "sign_consensus": sign_consensus,
        "hypothesis_direction": direction,
        "causal_trigger": True,
    }


def classify_spot_perp(
    spot_row: Mapping,
    perp_row: Mapping,
    thresholds: PatternThresholds | None = None,
) -> dict:
    """Classify same-asset spot/perp confirmation, opposition and one-sided lead."""
    cfg = thresholds or PatternThresholds()
    spot_z_raw = spot_row.get("equal_venue_zscore")
    perp_z_raw = perp_row.get("equal_venue_zscore")
    spot_z = float(spot_z_raw) if spot_z_raw is not None else None
    perp_z = float(perp_z_raw) if perp_z_raw is not None else None

    if spot_z is None or perp_z is None:
        label = "insufficient_history"
        direction = 0
    elif abs(spot_z) >= cfg.spot_perp_min_abs_z and abs(perp_z) >= cfg.spot_perp_min_abs_z:
        if spot_z * perp_z < 0:
            if perp_z > 0:
                label = "perp_buy_spot_sell_divergence"
                direction = 1
            else:
                label = "perp_sell_spot_buy_divergence"
                direction = -1
        else:
            label = "spot_perp_confirm_buy" if spot_z > 0 else "spot_perp_confirm_sell"
            direction = 1 if spot_z > 0 else -1
    elif abs(perp_z) >= cfg.spot_perp_min_abs_z and abs(spot_z) <= cfg.spot_perp_muted_abs_z:
        label = "perp_leads_buy" if perp_z > 0 else "perp_leads_sell"
        direction = 1 if perp_z > 0 else -1
    elif abs(spot_z) >= cfg.spot_perp_min_abs_z and abs(perp_z) <= cfg.spot_perp_muted_abs_z:
        label = "spot_leads_buy" if spot_z > 0 else "spot_leads_sell"
        direction = 1 if spot_z > 0 else -1
    else:
        label = "spot_perp_mixed"
        direction = 0

    return {
        "pattern_family": "spot_perp",
        "pattern_label": label,
        "asset": spot_row.get("asset") or perp_row.get("asset"),
        # Perpetual price is the preregistered outcome reference for spot/perp patterns.
        "market": "perp",
        "start_ms": spot_row.get("start_ms") or perp_row.get("start_ms"),
        "spot_zscore_past_only": spot_z,
        "perp_zscore_past_only": perp_z,
        "hypothesis_direction": direction,
        "causal_trigger": True,
    }


class LiquidationRegimeDetector:
    """Past-only extreme-liquidation regime detector.

    It labels an event only when current liquidation notional is extreme relative to
    prior completed windows. A 'driven_candidate' also requires the contemporaneous
    price move and aggressive flow to align with the forced side.
    """

    def __init__(
        self,
        thresholds: PatternThresholds | None = None,
        min_history: int = 200,
        history_maxlen: int = 20000,
    ) -> None:
        self.thresholds = thresholds or PatternThresholds()
        self._normalizers: dict[tuple[str, str], RollingNormalizer] = {}
        self.min_history = min_history
        self.history_maxlen = history_maxlen

    def _normalizer(self, asset: str, market: str) -> RollingNormalizer:
        key = (asset, market)
        if key not in self._normalizers:
            self._normalizers[key] = RollingNormalizer(
                maxlen=self.history_maxlen, min_history=self.min_history
            )
        return self._normalizers[key]

    def observe(
        self,
        *,
        asset: str,
        market: str,
        start_ms: int,
        long_liq_usd: float,
        short_liq_usd: float,
        price_return_bps: float,
        delta_flow_usd: float,
    ) -> dict | None:
        long_usd = max(float(long_liq_usd), 0.0)
        short_usd = max(float(short_liq_usd), 0.0)
        gross = long_usd + short_usd
        score = self._normalizer(asset, market).score_then_update(gross)
        if score.percentile is None or score.percentile < self.thresholds.liquidation_extreme_percentile:
            return None
        if gross <= 0:
            return None

        long_share = long_usd / gross
        short_share = short_usd / gross
        price = float(price_return_bps)
        flow = float(delta_flow_usd)

        if (
            long_share >= self.thresholds.liquidation_side_share
            and price <= -self.thresholds.liquidation_min_price_bps
            and flow < 0
        ):
            label = "long_liquidation_driven_candidate"
            direction = -1
        elif (
            short_share >= self.thresholds.liquidation_side_share
            and price >= self.thresholds.liquidation_min_price_bps
            and flow > 0
        ):
            label = "short_liquidation_driven_candidate"
            direction = 1
        else:
            label = "extreme_liquidation_mixed"
            direction = 0

        return {
            "pattern_family": "liquidation",
            "pattern_label": label,
            "asset": asset,
            "market": market,
            "start_ms": int(start_ms),
            "long_liq_usd": long_usd,
            "short_liq_usd": short_usd,
            "gross_liq_usd": gross,
            "long_liq_share": long_share,
            "short_liq_share": short_share,
            "liq_percentile_past_only": score.percentile,
            "liq_zscore_past_only": score.zscore,
            "price_return_bps": price,
            "delta_flow_usd": flow,
            "hypothesis_direction": direction,
            "causal_trigger": True,
        }


def join_spot_perp_rows(
    spot_rows: Sequence[Mapping],
    perp_rows: Sequence[Mapping],
    thresholds: PatternThresholds | None = None,
) -> list[dict]:
    """Join equal-venue composite rows by asset/start time and classify each pair."""
    perp_index = {
        (str(r.get("asset")), int(r.get("start_ms", -1))): r
        for r in perp_rows
    }
    out: list[dict] = []
    for spot in spot_rows:
        key = (str(spot.get("asset")), int(spot.get("start_ms", -1)))
        perp = perp_index.get(key)
        if perp is not None:
            out.append(classify_spot_perp(spot, perp, thresholds))
    return out

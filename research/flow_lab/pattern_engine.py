from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

from .absorption import orderbook_absorption_evidence
from .composite import EqualVenueComposite
from .exhaustion import CausalExhaustionDetector
from .patterns import (
    LiquidationRegimeDetector,
    PatternThresholds,
    classify_cross_venue,
    classify_spot_perp,
    response_pattern,
)
from .positioning import classify_oi_positioning
from .quality import StreamHealth, quality_gate
from .schema import FlowBucket


@dataclass(frozen=True, slots=True)
class OIWindow:
    exchange: str
    asset: str
    delta_oi_usd: float
    price_return_bps: float
    delta_flow_usd: float


@dataclass(frozen=True, slots=True)
class LiquidationWindow:
    asset: str
    market: str
    long_liq_usd: float
    short_liq_usd: float
    price_return_bps: float
    delta_flow_usd: float


@dataclass(frozen=True, slots=True)
class ExhaustionWindow:
    asset: str
    market: str
    bucket_index: int
    delta_usd: float
    abs_delta_percentile_past_only: float | None
    signed_impact_units_past_only: float | None


@dataclass(frozen=True, slots=True)
class BookEvidenceWindow:
    exchange: str
    market: str
    asset: str
    delta_usd: float
    return_bps: float
    impact_scale_bps: float | None
    depth_change: Mapping[str, float | bool]


@dataclass(slots=True)
class PatternEngine:
    """Stateful, research-only orchestrator for all Flow Lab pattern families.

    The engine does not trade. It applies one point-in-time data-quality gate, builds
    equal-weight venue composites from past-only normalizers, then emits eligible
    pattern events and diagnostics for the same completed window.

    Inputs must already be aligned to the same completed time bucket. Forward returns
    are intentionally absent here and belong only in `pattern_study.py`.
    """

    thresholds: PatternThresholds = field(default_factory=PatternThresholds)
    min_history: int = 200
    history_maxlen: int = 20000
    spot_composite: EqualVenueComposite = field(init=False)
    perp_composite: EqualVenueComposite = field(init=False)
    liquidation_detector: LiquidationRegimeDetector = field(init=False)
    exhaustion_detector: CausalExhaustionDetector = field(init=False)

    def __post_init__(self) -> None:
        self.spot_composite = EqualVenueComposite(self.min_history, self.history_maxlen)
        self.perp_composite = EqualVenueComposite(self.min_history, self.history_maxlen)
        self.liquidation_detector = LiquidationRegimeDetector(
            self.thresholds, self.min_history, self.history_maxlen
        )
        self.exhaustion_detector = CausalExhaustionDetector()

    @staticmethod
    def _single_composite(composite: EqualVenueComposite,
                          buckets: Sequence[FlowBucket]) -> dict | None:
        rows = composite.combine(buckets)
        if not rows:
            return None
        if len(rows) != 1:
            raise ValueError("PatternEngine expects one asset/market/time window per call")
        return rows[0]

    def evaluate(
        self,
        *,
        now_ms: int,
        stream_health: dict[str, StreamHealth],
        required_streams: tuple[str, ...],
        spot_buckets: Sequence[FlowBucket] = (),
        perp_buckets: Sequence[FlowBucket] = (),
        response_event: Mapping | None = None,
        oi_windows: Sequence[OIWindow] = (),
        liquidation_window: LiquidationWindow | None = None,
        exhaustion_window: ExhaustionWindow | None = None,
        book_windows: Sequence[BookEvidenceWindow] = (),
        max_stale_ms: int = 5000,
        max_event_lag_ms: int = 5000,
    ) -> dict:
        gate = quality_gate(
            stream_health,
            required_streams,
            now_ms,
            max_stale_ms=max_stale_ms,
            max_event_lag_ms=max_event_lag_ms,
        )

        # Normalizers are updated even when the quality gate fails only for rows that
        # callers chose to pass in. To keep invalid data out of reference history, do
        # not consume market buckets until the gate passes.
        if not gate["quality_pass"]:
            return {
                "evaluated_at_ms": int(now_ms),
                "quality": gate,
                "events": [],
                "diagnostics": {"suppressed_by_quality_gate": True},
            }

        spot = self._single_composite(self.spot_composite, spot_buckets) if spot_buckets else None
        perp = self._single_composite(self.perp_composite, perp_buckets) if perp_buckets else None
        events: list[dict] = []
        diagnostics: dict[str, object] = {
            "spot_composite": spot,
            "perp_composite": perp,
            "book_evidence": [],
            "oi_context": [],
        }

        for row in (spot, perp):
            if row is None:
                continue
            event = classify_cross_venue(row, self.thresholds)
            if event["pattern_label"] not in {"insufficient_venues", "cross_venue_mixed"}:
                events.append(event)

        if spot is not None and perp is not None:
            sp = classify_spot_perp(spot, perp, self.thresholds)
            diagnostics["spot_perp"] = sp
            if sp["pattern_label"] not in {"insufficient_history", "spot_perp_mixed"}:
                events.append(sp)

        if response_event is not None:
            rp = response_pattern(response_event)
            diagnostics["response_pattern"] = rp
            if rp is not None:
                events.append(rp)

        for oi in oi_windows:
            ctx = classify_oi_positioning(
                oi.price_return_bps,
                oi.delta_oi_usd,
                oi.delta_flow_usd,
            )
            ctx.update({"exchange": oi.exchange, "asset": oi.asset})
            diagnostics["oi_context"].append(ctx)
            if ctx["pattern_label"] not in {"flat_oi_or_noise", "mixed_positioning"}:
                events.append(ctx)

        if liquidation_window is not None:
            lw = liquidation_window
            liq = self.liquidation_detector.observe(
                asset=lw.asset,
                market=lw.market,
                start_ms=int(response_event.get("start_ms", now_ms)) if response_event else int(now_ms),
                long_liq_usd=lw.long_liq_usd,
                short_liq_usd=lw.short_liq_usd,
                price_return_bps=lw.price_return_bps,
                delta_flow_usd=lw.delta_flow_usd,
            )
            diagnostics["liquidation"] = liq
            if liq is not None:
                events.append(liq)

        if exhaustion_window is not None:
            ew = exhaustion_window
            exhaustion = self.exhaustion_detector.observe(
                ew.asset,
                ew.market,
                ew.bucket_index,
                ew.delta_usd,
                ew.abs_delta_percentile_past_only,
                ew.signed_impact_units_past_only,
            )
            diagnostics["exhaustion"] = exhaustion
            if exhaustion is not None:
                events.append(exhaustion)

        for bw in book_windows:
            evidence = orderbook_absorption_evidence(
                bw.delta_usd,
                bw.return_bps,
                bw.impact_scale_bps,
                bw.depth_change,
            )
            evidence.update({
                "exchange": bw.exchange,
                "market": bw.market,
                "asset": bw.asset,
            })
            diagnostics["book_evidence"].append(evidence)

        return {
            "evaluated_at_ms": int(now_ms),
            "quality": gate,
            "events": events,
            "diagnostics": diagnostics,
        }

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .cluster_engine import ClusterAwareGalkaLiveEngine


def _positive_int(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0


class V3ClusterAwareGalkaLiveEngine(ClusterAwareGalkaLiveEngine):
    """Accept the V3 manual GALKA annotation without changing trading logic.

    V3 keeps the same research coordinate model as V2, but adds an explicit
    anchor-selection stage and free XY left/right boundary selection. The base
    research engine already validates/sanitizes all V2-compatible coordinates,
    so V3 is normalized through that path and then its provenance fields are
    restored for Detective/research consumers.
    """

    def _normalize_research_setup(
        self,
        coin: str,
        galka_price: float,
        setup: Any,
    ) -> dict[str, Any] | None:
        if not isinstance(setup, dict):
            return super()._normalize_research_setup(coin, galka_price, setup)

        method = str(setup.get("selectionMethod") or "")
        if method != "manual_crosshair_structure_v3":
            return super()._normalize_research_setup(coin, galka_price, setup)

        anchor_ms = _positive_int(setup.get("anchorTimeMs"))
        left_ms = _positive_int(setup.get("leftBoundaryTimeMs") or setup.get("structureStartTimeMs"))
        right_ms = _positive_int(setup.get("rightBoundaryTimeMs") or setup.get("structureEndTimeMs"))
        if not (left_ms < anchor_ms < right_ms):
            return None

        compatible = deepcopy(setup)
        compatible["selectionMethod"] = "manual_crosshair_structure_v2"
        normalized = super()._normalize_research_setup(coin, galka_price, compatible)
        if normalized is None:
            return None

        normalized["selectionMethod"] = "manual_crosshair_structure_v3"
        normalized["anchorPlacementMethod"] = str(
            setup.get("anchorPlacementMethod") or "manual_horizontal_snap_to_candle"
        )
        normalized["boundaryPlacementMethod"] = str(
            setup.get("boundaryPlacementMethod") or "manual_free_xy"
        )
        normalized["anchorSelectedAtMs"] = _positive_int(setup.get("anchorSelectedAtMs"))
        normalized["leftSelectedAtMs"] = _positive_int(setup.get("leftSelectedAtMs"))
        normalized["rightSelectedAtMs"] = _positive_int(setup.get("rightSelectedAtMs"))
        normalized["interactionModel"] = "persistent_relative_boundary_adjustment"
        return normalized

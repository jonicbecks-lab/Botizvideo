from __future__ import annotations

from copy import deepcopy
from typing import Any

from .cluster_engine import ClusterAwareGalkaLiveEngine
from .engine import LiveEngineError


def _positive_int(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0


class V3ClusterAwareGalkaLiveEngine(ClusterAwareGalkaLiveEngine):
    """Accept V3 research geometry, expose read-only observation and protect LIVE writes."""

    def __init__(self, config: Any, gateway: Any):
        self.agent_readonly_api = None
        self._agent_api_error: str | None = None
        super().__init__(config, gateway)

    def start(self) -> None:
        super().start()
        try:
            from .agent_api import AgentReadOnlyAPIServer

            api = AgentReadOnlyAPIServer(self)
            api.start()
            self.agent_readonly_api = api
            self._agent_api_error = None
            with self.lock:
                self._event_locked(
                    "research",
                    f"Read-only Agent API запущен на отдельном порту {api.port}",
                    port=api.port,
                    readOnly=True,
                )
                self._save_locked()
        except Exception as exc:
            # Observation must never block trading startup. The browser Control
            # Center can surface this error while GALKA continues in its normal
            # fail-closed trading mode.
            self._agent_api_error = f"{type(exc).__name__}: {exc}"
            with self.lock:
                self._event_locked(
                    "research",
                    f"Read-only Agent API не запущен: {self._agent_api_error}",
                    readOnly=True,
                )
                self._save_locked()

    def stop(self) -> None:
        api = self.agent_readonly_api
        if api is not None:
            try:
                api.stop()
            except Exception:
                pass
        self.agent_readonly_api = None
        super().stop()

    def agent_api_status(self, *, include_token: bool = False) -> dict[str, Any]:
        api = self.agent_readonly_api
        if api is not None:
            return api.status(include_token=include_token)
        return {
            "enabled": True,
            "running": False,
            "readOnly": True,
            "error": self._agent_api_error or "Agent API has not started yet",
        }

    def _require_live_writes(self) -> None:
        super()._require_live_writes()
        guard = self.config.data_dir / "runtime" / "updater.active"
        if guard.exists():
            raise LiveEngineError("GALKA обновляется/перезапускается; торговые команды временно заблокированы")

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

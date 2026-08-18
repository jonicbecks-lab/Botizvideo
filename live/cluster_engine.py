from __future__ import annotations

from typing import Any

from .auto_queue_engine import AutoQueueGalkaLiveEngine
from .cluster_volume import ClusterVolumeService


class ClusterAwareGalkaLiveEngine(AutoQueueGalkaLiveEngine):
    """AUTO+research engine with an isolated display-only cluster volume feed."""

    def __init__(self, config: Any, gateway: Any):
        self.cluster_volume = ClusterVolumeService(config)
        super().__init__(config, gateway)

    def start(self) -> None:
        try:
            self.cluster_volume.start()
        except Exception:
            # Chart telemetry must never block LIVE trading startup.
            pass
        super().start()

    def stop(self) -> None:
        try:
            self.cluster_volume.stop()
        finally:
            super().stop()

    def cluster_snapshot(
        self,
        coin: str,
        interval: str,
        aggregation: str = "auto",
    ) -> dict[str, Any]:
        return self.cluster_volume.snapshot(coin, interval, aggregation)

    def status(self) -> dict[str, Any]:
        result = super().status()
        try:
            result["clusterVolume"] = self.cluster_volume.status()
        except Exception:
            result["clusterVolume"] = {"enabled": False, "error": "status unavailable"}
        return result

from __future__ import annotations

from typing import Any

from .auto_queue_engine import AutoQueueGalkaLiveEngine
from .cluster_archive import PersistentClusterVolumeService


class ClusterAwareGalkaLiveEngine(AutoQueueGalkaLiveEngine):
    """AUTO+research engine with isolated persistent chart-cluster telemetry."""

    def __init__(self, config: Any, gateway: Any):
        self.cluster_volume = PersistentClusterVolumeService(config)
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
        from_ms: int | None = None,
        to_ms: int | None = None,
    ) -> dict[str, Any]:
        return self.cluster_volume.snapshot(coin, interval, aggregation, from_ms, to_ms)

    def status(self) -> dict[str, Any]:
        result = super().status()
        try:
            result["clusterVolume"] = self.cluster_volume.status()
        except Exception:
            result["clusterVolume"] = {"enabled": False, "error": "status unavailable"}
        return result

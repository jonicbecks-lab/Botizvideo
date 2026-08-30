from __future__ import annotations

from typing import Any

from .auto_queue_engine import AutoQueueGalkaLiveEngine
from .cluster_archive import PersistentClusterVolumeService
from .cluster_volume import ClusterVolumeService


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

    @staticmethod
    def _balance_auto_threshold(result: dict[str, Any]) -> dict[str, Any]:
        """Keep AUTO useful on history instead of letting one recent spike hide it.

        The browser currently reads the q90 field for AUTO. For persistent history
        we deliberately expose q75 there: manual mode still uses the exact USD
        threshold slider, while AUTO shows a broader set of historically large
        clusters across the visible range.
        """
        summaries = result.get("summaryByMetric")
        if not isinstance(summaries, dict):
            return result
        for summary in summaries.values():
            if not isinstance(summary, dict):
                continue
            try:
                q75 = float(summary.get("q75") or 0)
            except (TypeError, ValueError):
                q75 = 0.0
            if q75 > 0:
                summary["q90"] = q75
        result["autoFilterPolicy"] = "history_balanced_q75"
        return result

    def cluster_snapshot(
        self,
        coin: str,
        interval: str,
        aggregation: str = "auto",
        from_ms: int | None = None,
        to_ms: int | None = None,
    ) -> dict[str, Any]:
        result = self.cluster_volume.snapshot(coin, interval, aggregation, from_ms, to_ms)
        return self._balance_auto_threshold(result)

    def status(self) -> dict[str, Any]:
        """Return a cheap trading/UI status snapshot.

        PersistentClusterVolumeService.status() also scans the on-disk cluster
        archive periodically. That work is useful for diagnostics, but putting it
        on every /api/live/status request made a research feature capable of
        delaying the trading UI as the archive grew. The cluster endpoint already
        returns its own stream/history metadata, so the main LIVE status only needs
        the in-memory websocket counters here.
        """
        result = super().status()
        try:
            # Deliberately call the lightweight base implementation and bypass
            # PersistentClusterVolumeService.status(), which adds archive scans.
            result["clusterVolume"] = ClusterVolumeService.status(self.cluster_volume)
            result["clusterVolume"]["archivePersistent"] = True
        except Exception:
            result["clusterVolume"] = {"enabled": False, "error": "status unavailable"}
        return result

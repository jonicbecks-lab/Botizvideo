from __future__ import annotations

from http import HTTPStatus
from urllib.parse import parse_qs, urlparse

from . import persistent_server as _persistent
from .cluster_engine import ClusterAwareGalkaLiveEngine
from .engine import LiveEngineError


class AutoQueueGalkaRequestHandler(_persistent.PersistentGalkaRequestHandler):
    """Persistent LIVE handler plus AUTO queue and chart-cluster controls."""

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        parsed = urlparse(self.path)
        if parsed.path not in {"/api/live/queue", "/api/live/clusters"}:
            super().do_GET()
            return
        if not self._require_api_auth():
            return
        query = parse_qs(parsed.query)
        coin = query.get("coin", [""])[0]
        if parsed.path == "/api/live/queue":
            self._handle(lambda: self.engine.queued_galka_status(coin))
            return
        interval = query.get("interval", ["5m"])[0]
        aggregation = query.get("aggregation", ["auto"])[0]
        self._handle(lambda: self.engine.cluster_snapshot(coin, interval, aggregation))

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        parsed = urlparse(self.path)
        if parsed.path not in {
            "/api/live/queue",
            "/api/live/queue/activate",
            "/api/live/queue/delete",
        }:
            super().do_POST()
            return
        if not self._require_api_auth():
            return
        try:
            data = self._read_json()
        except LiveEngineError as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
            return

        if parsed.path == "/api/live/queue":
            self._handle(
                lambda: self.engine.queue_next_galka(
                    str(data.get("coin", "")),
                    float(data.get("galkaPrice", 0)),
                    str(data.get("confirmation", "")),
                )
            )
            return
        if parsed.path == "/api/live/queue/activate":
            self._handle(
                lambda: self.engine.activate_queued_galka(
                    str(data.get("coin", "")),
                    str(data.get("confirmation", "")),
                )
            )
            return
        self._handle(
            lambda: self.engine.delete_queued_galka(
                str(data.get("coin", "")),
                str(data.get("confirmation", "")),
            )
        )


# Reuse the proven persistent HTTP/session/PID server unchanged. Only substitute
# the handler with the local-only extra endpoints and the engine with the
# cluster+AUTO+research subclass. Trading routes/authentication remain unchanged.
_persistent.PersistentGalkaRequestHandler = AutoQueueGalkaRequestHandler
_persistent.SafeCompatibleGalkaLiveEngine = ClusterAwareGalkaLiveEngine


def main() -> int:
    return _persistent.main()


if __name__ == "__main__":
    raise SystemExit(main())

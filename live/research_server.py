from __future__ import annotations

import threading
import time
from http import HTTPStatus
from urllib.parse import parse_qs, urlparse

from . import persistent_server as _persistent
from .cluster_engine import ClusterAwareGalkaLiveEngine
from .engine import LiveEngineError
from .hyperliquid_gateway import INTERVAL_MS, GatewayError, _finite_number, _integer
from .hyperliquid_safe_compat import SafeCompatibleHyperliquidGateway as _TradingGateway


def _optional_int(query: dict[str, list[str]], name: str) -> int | None:
    raw = query.get(name, [""])[0]
    if raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise LiveEngineError(f"Некорректный параметр {name}") from exc


class PublicMarketIsolatedGateway(_TradingGateway):
    """Keep heavy public candle snapshots off the trading/private I/O lock.

    The base gateway intentionally serializes its authenticated reads and writes.
    The chart previously used that same lock for 600/1500-bar public candle
    snapshots, so a timeframe switch could delay a real cancel/order operation.
    A separate read-only Hyperliquid Info client removes that contention while all
    trading/account/order methods remain on the proven gateway path unchanged.
    """

    def __init__(self, config):
        super().__init__(config)
        from hyperliquid.info import Info

        self._chart_info = Info(self.base_url, skip_ws=True, timeout=config.request_timeout)
        self._chart_info_lock = threading.RLock()

    def candles(self, coin: str, interval: str, limit: int = 1000) -> list[dict]:
        normalized = self._coin(coin)
        if interval not in INTERVAL_MS:
            raise GatewayError(f"Unsupported interval: {interval}")
        limit = max(50, min(int(limit), 1500))
        end_ms = int(time.time() * 1000)
        start_ms = end_ms - INTERVAL_MS[interval] * (limit + 5)
        try:
            with self._chart_info_lock:
                rows = self._chart_info.candles_snapshot(
                    normalized,
                    interval,
                    start_ms,
                    end_ms,
                )[-limit:]
        except Exception as exc:
            raise GatewayError(f"Hyperliquid read failed (candles_snapshot): {exc}") from exc
        return [
            {
                "time": _integer(row.get("t"), "candle.t") // 1000,
                "openTime": _integer(row.get("t"), "candle.t"),
                "closeTime": _integer(row.get("T"), "candle.T"),
                "open": _finite_number(row.get("o"), "candle.o"),
                "high": _finite_number(row.get("h"), "candle.h"),
                "low": _finite_number(row.get("l"), "candle.l"),
                "close": _finite_number(row.get("c"), "candle.c"),
                "volume": _finite_number(row.get("v"), "candle.v"),
            }
            for row in rows
        ]


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
        try:
            from_ms = _optional_int(query, "fromMs")
            to_ms = _optional_int(query, "toMs")
        except LiveEngineError as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
            return
        self._handle(
            lambda: self.engine.cluster_snapshot(
                coin,
                interval,
                aggregation,
                from_ms,
                to_ms,
            )
        )

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


# Reuse the proven persistent HTTP/session/PID server. Substitute only the local
# extra endpoints, cluster/research engine, and a read-only public candle client.
# All trading routes/authentication/signing stay on the existing gateway methods.
_persistent.PersistentGalkaRequestHandler = AutoQueueGalkaRequestHandler
_persistent.SafeCompatibleGalkaLiveEngine = ClusterAwareGalkaLiveEngine
_persistent.SafeCompatibleHyperliquidGateway = PublicMarketIsolatedGateway


def main() -> int:
    return _persistent.main()


if __name__ == "__main__":
    raise SystemExit(main())
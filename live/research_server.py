from __future__ import annotations

import threading
import time
from http import HTTPStatus
from urllib.parse import parse_qs, urlparse

from . import persistent_server as _persistent
from .cluster_volume import BASE_PRICE_STEPS
from .engine import LiveEngineError
from .hyperliquid_gateway import (
    INTERVAL_MS,
    SUPPORTED_COINS,
    GatewayError,
    _finite_number,
    _integer,
)
from .hyperliquid_safe_compat import SafeCompatibleHyperliquidGateway as _TradingGateway
from .research_v3_engine import V3ClusterAwareGalkaLiveEngine
from .update_manager_v2 import manager_for


# Production Galka LIVE universe. Keep the shared set object instead of rebinding it:
# engine/cluster/queue modules import the same mutable set during module loading.
SUPPORTED_COINS.clear()
SUPPORTED_COINS.update({"BTC", "ETH", "BNB"})
BASE_PRICE_STEPS.pop("SOL", None)
BASE_PRICE_STEPS["BNB"] = 0.1


def _optional_int(query: dict[str, list[str]], name: str) -> int | None:
    raw = query.get(name, [""])[0]
    if raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise LiveEngineError(f"Некорректный параметр {name}") from exc


class PublicMarketIsolatedGateway(_TradingGateway):
    """Keep display reads off the private trading I/O path."""

    STATUS_ACCOUNT_CACHE_SECONDS = 30.0
    QUOTE_CACHE_SECONDS = 0.8
    CANDLE_CACHE_SECONDS = 2.0

    def __init__(self, config):
        super().__init__(config)
        from hyperliquid.info import Info

        self._chart_info = Info(self.base_url, skip_ws=True, timeout=config.request_timeout)
        self._quote_info = Info(self.base_url, skip_ws=True, timeout=config.request_timeout)
        self._chart_info_lock = threading.RLock()
        self._quote_info_lock = threading.RLock()
        self._quote_cache_at = 0.0
        self._quote_cache: dict[str, float] = {}
        self._candle_cache: dict[tuple[str, str, int], tuple[float, list[dict]]] = {}

    def account_state(self, fresh: bool = False) -> dict:
        if fresh:
            return super().account_state(fresh=True)
        cached = self._cache_get("account_state", self.STATUS_ACCOUNT_CACHE_SECONDS)
        if cached is not None:
            return cached
        return super().account_state(fresh=False)

    def mids(self) -> dict[str, float]:
        now = time.monotonic()
        with self._quote_info_lock:
            if self._quote_cache and now - self._quote_cache_at < self.QUOTE_CACHE_SECONDS:
                return dict(self._quote_cache)
            try:
                rows = self._quote_info.all_mids()
            except Exception as exc:
                raise GatewayError(f"Hyperliquid read failed (all_mids): {exc}") from exc
            result = {
                coin: _finite_number(rows[coin], f"mids.{coin}")
                for coin in SUPPORTED_COINS
                if coin in rows
            }
            invalid = [coin for coin, value in result.items() if value <= 0]
            if invalid:
                raise GatewayError(f"Invalid non-positive mids: {invalid}")
            self._quote_cache = result
            self._quote_cache_at = time.monotonic()
            return dict(result)

    def candles(self, coin: str, interval: str, limit: int = 1000) -> list[dict]:
        normalized = self._coin(coin)
        if interval not in INTERVAL_MS:
            raise GatewayError(f"Unsupported interval: {interval}")
        limit = max(50, min(int(limit), 1500))
        key = (normalized, interval, limit)
        now = time.monotonic()

        with self._chart_info_lock:
            cached = self._candle_cache.get(key)
            if cached and now - cached[0] < self.CANDLE_CACHE_SECONDS:
                return [dict(row) for row in cached[1]]

            end_ms = int(time.time() * 1000)
            start_ms = end_ms - INTERVAL_MS[interval] * (limit + 5)
            try:
                rows = self._chart_info.candles_snapshot(
                    normalized,
                    interval,
                    start_ms,
                    end_ms,
                )[-limit:]
            except Exception as exc:
                raise GatewayError(f"Hyperliquid read failed (candles_snapshot): {exc}") from exc

            result = [
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
            self._candle_cache[key] = (time.monotonic(), result)
            if len(self._candle_cache) > 18:
                oldest = min(self._candle_cache, key=lambda item: self._candle_cache[item][0])
                self._candle_cache.pop(oldest, None)
            return [dict(row) for row in result]


class AutoQueueGalkaRequestHandler(_persistent.PersistentGalkaRequestHandler):
    """Persistent LIVE handler plus AUTO queue, research annotations, clusters and updater."""

    _foreground_guard = threading.RLock()
    _foreground_requests = 0
    _foreground_paths = {
        "/api/live/preview",
        "/api/live/campaign",
        "/api/live/reconcile",
        "/api/live/close-near-market",
        "/api/live/emergency",
    }
    _updater_post_paths = {
        "/api/live/updater/check",
        "/api/live/updater/update",
        "/api/live/updater/restart",
        "/api/live/updater/rollback",
    }

    @classmethod
    def _foreground_enter(cls, engine) -> None:
        pending = getattr(engine, "_manual_action_pending", None)
        if pending is None:
            return
        with cls._foreground_guard:
            cls._foreground_requests += 1
            pending.set()

    @classmethod
    def _foreground_exit(cls, engine) -> None:
        pending = getattr(engine, "_manual_action_pending", None)
        if pending is None:
            return
        with cls._foreground_guard:
            cls._foreground_requests = max(0, cls._foreground_requests - 1)
            if cls._foreground_requests == 0:
                pending.clear()

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        parsed = urlparse(self.path)
        if parsed.path == "/api/live/updater/status":
            if not self._require_api_auth():
                return
            self._handle(lambda: manager_for(self.engine).status())
            return
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

    def _handle_campaign_with_research(self) -> None:
        if not self._require_api_auth():
            return
        try:
            data = self._read_json()
        except LiveEngineError as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
            return

        self._foreground_enter(self.engine)
        try:
            self._handle(
                lambda: self.engine.create_campaign(
                    str(data.get("coin", "")),
                    float(data.get("galkaPrice", 0)),
                    str(data.get("confirmation", "")),
                    data.get("researchSetup"),
                )
            )
        finally:
            self._foreground_exit(self.engine)

    def _handle_updater(self, path: str) -> None:
        if not self._require_api_auth():
            return
        try:
            data = self._read_json()
        except LiveEngineError as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
            return
        updater = manager_for(self.engine)
        if path == "/api/live/updater/check":
            self._handle(updater.check)
            return
        if path == "/api/live/updater/update":
            self._handle(lambda: updater.install(str(data.get("confirmation", ""))))
            return
        if path == "/api/live/updater/restart":
            self._handle(lambda: updater.restart(str(data.get("confirmation", ""))))
            return
        self._handle(lambda: updater.rollback(str(data.get("confirmation", ""))))

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        parsed = urlparse(self.path)

        if parsed.path in self._updater_post_paths:
            self._handle_updater(parsed.path)
            return

        # The production research engine accepts an optional immutable manual
        # GALKA structure. Old clients omit it and keep the legacy placement path.
        if parsed.path == "/api/live/campaign":
            self._handle_campaign_with_research()
            return

        if parsed.path in self._foreground_paths:
            self._foreground_enter(self.engine)
            try:
                super().do_POST()
            finally:
                self._foreground_exit(self.engine)
            return

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
            self._foreground_enter(self.engine)
            try:
                self._handle(
                    lambda: self.engine.activate_queued_galka(
                        str(data.get("coin", "")),
                        str(data.get("confirmation", "")),
                    )
                )
            finally:
                self._foreground_exit(self.engine)
            return
        self._handle(
            lambda: self.engine.delete_queued_galka(
                str(data.get("coin", "")),
                str(data.get("confirmation", "")),
            )
        )


_persistent.PersistentGalkaRequestHandler = AutoQueueGalkaRequestHandler
_persistent.SafeCompatibleGalkaLiveEngine = V3ClusterAwareGalkaLiveEngine
_persistent.SafeCompatibleHyperliquidGateway = PublicMarketIsolatedGateway

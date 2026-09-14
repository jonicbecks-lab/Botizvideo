from __future__ import annotations

import secrets
import sys
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from urllib.parse import urlparse

from . import hyperliquid_gateway
from .config import ConfigError, load_config
from .engine import LiveEngineError
from .galka_v2_engine import GalkaV2Engine, GalkaV2Gateway
from .galka_v2_strategy import V2_LEVERAGE, V2_MARGIN_USD, V2_TOTAL_NOTIONAL
from .hyperliquid_gateway import GatewayError
from .server import GalkaRequestHandler, LiveProcessLock
from .tpsl_batch_compat import install as install_tpsl_batch_compat

V2_COINS = {"BTC", "ETH", "BNB"}


def install_v2_runtime() -> None:
    """Apply V2-only runtime compatibility without mutating the old GALKA branch."""
    # engine.py imported the same mutable set object, so mutate in place.
    hyperliquid_gateway.SUPPORTED_COINS.clear()
    hyperliquid_gateway.SUPPORTED_COINS.update(V2_COINS)
    # Adds BNB to the inherited near-market emergency/quick-close step map.
    install_tpsl_batch_compat()


class GalkaV2RequestHandler(GalkaRequestHandler):
    """Browser bootstrap for the local-only V2 server.

    /open intentionally contains no bearer token. It only mints the HttpOnly
    session cookie for a request whose Host is the loopback server itself. API
    calls remain protected by the normal same-origin checks and session cookie.
    This avoids stale one-time URLs when Android reopens/restores a browser tab.
    """

    def _loopback_host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").lower()
        return host in {
            f"127.0.0.1:{self.server_port}",
            f"localhost:{self.server_port}",
        }

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path.rstrip("/") == "/open":
            if not self._loopback_host_ok():
                return self._json(
                    HTTPStatus.UNAUTHORIZED,
                    {"ok": False, "error": "Недействительная локальная LIVE-сессия"},
                )
            return self._send_session_cookie(redirect=True)
        return super().do_GET()


def main() -> int:
    lock = None
    engine = None
    server = None
    try:
        install_v2_runtime()
        config = load_config()
        lock = LiveProcessLock(config.data_dir)
        lock.acquire()
        gateway = GalkaV2Gateway(config)
        engine = GalkaV2Engine(config, gateway)

        # The token never appears in the browser URL. It only backs the local
        # HttpOnly session cookie set by GET /open.
        token = secrets.token_urlsafe(32)
        GalkaV2RequestHandler.engine = engine
        GalkaV2RequestHandler.session_token = token
        GalkaV2RequestHandler.server_port = config.port
        server = ThreadingHTTPServer((config.host, config.port), GalkaV2RequestHandler)
        server.daemon_threads = True
        engine.start()
    except (ConfigError, RuntimeError, GatewayError, LiveEngineError, OSError) as exc:
        if engine is not None:
            engine.stop()
        if server is not None:
            server.server_close()
        if lock is not None:
            lock.release()
        print(f"Galka V2 не запущена: {exc}", file=sys.stderr, flush=True)
        return 2

    open_url = f"http://{config.host}:{config.port}/open"
    base_url = f"http://{config.host}:{config.port}/terminal/live.html"
    print(f"Galka V2 URL: {open_url}", flush=True)
    print(f"После входа браузер сам откроет: {base_url}", flush=True)
    print(f"Сеть: {config.network_name} · аккаунт {config.masked_address}", flush=True)
    print(f"Режим: {'LIVE ENABLED' if config.live_enabled else 'READ ONLY'}", flush=True)
    print(
        f"GALKA V2: {V2_LEVERAGE}x isolated · маржа до ${V2_MARGIN_USD:.2f} · "
        f"номинал ${V2_TOTAL_NOTIONAL:.2f}",
        flush=True,
    )
    print("Локальная браузерная сессия создаётся без секретов в URL.", flush=True)

    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        engine.stop()
        server.server_close()
        lock.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

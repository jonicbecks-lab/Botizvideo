from __future__ import annotations

import json
import os
import secrets
import stat
import sys
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from . import hyperliquid_gateway
from .config import ConfigError, load_config
from .engine import LiveEngineError
from .galka_classic_engine import GalkaClassicEngine, GalkaClassicGateway
from .hyperliquid_gateway import GatewayError
from .server import GalkaRequestHandler, LiveProcessLock
from .tpsl_batch_compat import install as install_tpsl_batch_compat

CLASSIC_COINS = {"BTC", "ETH", "BNB"}
# Backward-compatible names used by existing tests and launcher helpers.
V2_COINS = CLASSIC_COINS
COOKIE_MAX_AGE = 30 * 24 * 60 * 60


def install_classic_runtime() -> None:
    """Apply mobile runtime compatibility while preserving the old branch."""
    hyperliquid_gateway.SUPPORTED_COINS.clear()
    hyperliquid_gateway.SUPPORTED_COINS.update(CLASSIC_COINS)
    install_tpsl_batch_compat()


def install_v2_runtime() -> None:
    """Backward-compatible alias; runtime now starts GALKA CLASSIC."""
    install_classic_runtime()


def load_or_create_v2_session_token(data_dir: Path) -> str:
    """Keep the local browser session stable across safe restarts."""
    runtime_dir = data_dir / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    try:
        runtime_dir.chmod(0o700)
    except OSError:
        pass

    path = runtime_dir / "browser-session-v2.token"
    if path.exists() or path.is_symlink():
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise LiveEngineError("Не удалось проверить локальную сессию GALKA") from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise LiveEngineError("Небезопасный файл локальной сессии GALKA")
        try:
            path.chmod(0o600)
            token = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise LiveEngineError("Не удалось прочитать локальную сессию GALKA") from exc
        if len(token) < 32:
            raise LiveEngineError("Повреждён файл локальной сессии GALKA")
        return token

    token = secrets.token_urlsafe(48)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
        try:
            os.write(descriptor, f"{token}\n".encode("ascii"))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        path.chmod(0o600)
    except OSError as exc:
        raise LiveEngineError("Не удалось создать локальную сессию GALKA") from exc
    return token


class GalkaV2RequestHandler(GalkaRequestHandler):
    """Tokenless local bootstrap with a port-specific HttpOnly cookie."""

    def _loopback_host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").lower()
        return host in {
            f"127.0.0.1:{self.server_port}",
            f"localhost:{self.server_port}",
        }

    def _v2_cookie_name(self) -> str:
        return f"GalkaV2Session{self.server_port}"

    def _cookie_session(self) -> str:
        raw = self.headers.get("Cookie") or ""
        if not raw:
            return ""
        try:
            cookies = SimpleCookie()
            cookies.load(raw)
            morsel = cookies.get(self._v2_cookie_name())
            return morsel.value if morsel else ""
        except Exception:
            return ""

    def _send_session_cookie(self, *, redirect: bool = False) -> None:
        cookie = (
            f"{self._v2_cookie_name()}={self.session_token}; Path=/; HttpOnly; "
            f"SameSite=Strict; Max-Age={COOKIE_MAX_AGE}"
        )
        if redirect:
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", "/terminal/live.html")
            self.send_header("Set-Cookie", cookie)
            self.end_headers()
            return

        body = json.dumps({"ok": True}, ensure_ascii=False).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

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
        install_classic_runtime()
        config = load_config()
        lock = LiveProcessLock(config.data_dir)
        lock.acquire()
        gateway = GalkaClassicGateway(config)
        engine = GalkaClassicEngine(config, gateway)

        token = load_or_create_v2_session_token(config.data_dir)
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
        print(f"Galka CLASSIC не запущена: {exc}", file=sys.stderr, flush=True)
        return 2

    open_url = f"http://{config.host}:{config.port}/open"
    base_url = f"http://{config.host}:{config.port}/terminal/live.html"
    print(f"Galka URL: {open_url}", flush=True)
    print(f"После входа браузер сам откроет: {base_url}", flush=True)
    print(f"Сеть: {config.network_name} · аккаунт {config.masked_address}", flush=True)
    print(f"Режим: {'LIVE ENABLED' if config.live_enabled else 'READ ONLY'}", flush=True)
    print(
        f"GALKA CLASSIC: {config.leverage}x isolated · маржа рассчитывается по максимуму из свободного баланса",
        flush=True,
    )
    print("L1-L8: 42% / 22% / 12% / 8% / 6% / 4% / 3% / 3%.", flush=True)
    print("Оставляется только резерв на входные комиссии и технический запас.", flush=True)
    print("Автоматический L1 rearm отключён: после возврата на GALKA кампания завершается.", flush=True)

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

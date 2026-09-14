from __future__ import annotations

import hmac
import json
import os
import secrets
import signal
import stat
import sys
import threading
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .config import ConfigError, load_config
from .engine import LiveEngineError
from .hyperliquid_gateway import GatewayError
from .hyperliquid_safe_compat import (
    SafeCompatibleGalkaLiveEngine,
    SafeCompatibleHyperliquidGateway,
)
from .server import GalkaRequestHandler, LiveProcessLock

COOKIE_MAX_AGE = 30 * 24 * 60 * 60
MAX_SESSION_BODY_BYTES = 4096


def _private_regular_file(path: Path) -> None:
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise LiveEngineError(f"Небезопасный служебный файл LIVE: {path}")
    os.chmod(path, 0o600)


def load_or_create_session_token(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    if path.exists() or path.is_symlink():
        _private_regular_file(path)
        token = path.read_text(encoding="utf-8").strip()
        if len(token) < 32:
            raise LiveEngineError("Повреждён локальный токен браузерной LIVE-сессии")
        return token

    token = secrets.token_urlsafe(48)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        os.write(descriptor, f"{token}\n".encode("ascii"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _private_regular_file(path)
    return token


def write_pid_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    if path.is_symlink():
        raise LiveEngineError("LIVE PID-файл не должен быть символической ссылкой")
    temporary = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    if temporary.exists() or temporary.is_symlink():
        temporary.unlink()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(temporary, flags, 0o600)
    try:
        os.write(descriptor, f"{os.getpid()}\n".encode("ascii"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, path)
    os.chmod(path, 0o600)


class PersistentGalkaRequestHandler(GalkaRequestHandler):
    cookie_name: str

    def _valid_request_context(self) -> bool:
        host = (self.headers.get("Host") or "").lower()
        allowed_hosts = {
            f"127.0.0.1:{self.server_port}",
            f"localhost:{self.server_port}",
        }
        if host not in allowed_hosts:
            return False
        origin = self.headers.get("Origin")
        if origin and origin not in self._allowed_origins():
            return False
        fetch_site = (self.headers.get("Sec-Fetch-Site") or "").lower()
        return not fetch_site or fetch_site in {"same-origin", "none"}

    def _session_cookie(self) -> str:
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except Exception:
            return ""
        morsel = cookie.get(self.cookie_name)
        return morsel.value if morsel is not None else ""

    def _authorized_api_request(self) -> bool:
        if not self._valid_request_context():
            return False
        supplied_header = self.headers.get("X-Galka-Session") or ""
        supplied_cookie = self._session_cookie()
        header_ok = bool(supplied_header) and hmac.compare_digest(supplied_header, self.session_token)
        cookie_ok = bool(supplied_cookie) and hmac.compare_digest(supplied_cookie, self.session_token)
        return header_ok or cookie_ok

    def _json(self, status: int, payload: dict | list, *, session_cookie: str | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if session_cookie is not None:
            self.send_header(
                "Set-Cookie",
                f"{self.cookie_name}={session_cookie}; HttpOnly; SameSite=Strict; Path=/; Max-Age={COOKIE_MAX_AGE}",
            )
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        parsed = urlparse(self.path)
        if parsed.path == "/healthz":
            self._json(
                HTTPStatus.OK,
                {"ok": True, "server": "galka-live", "pid": os.getpid()},
            )
            return
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        parsed = urlparse(self.path)
        if parsed.path != "/api/live/session":
            super().do_POST()
            return
        if not self._valid_request_context():
            self._json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "Недопустимый источник LIVE-сессии"})
            return
        try:
            content_length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            content_length = 0
        if content_length <= 0 or content_length > MAX_SESSION_BODY_BYTES:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Некорректное тело LIVE-сессии"})
            return
        try:
            payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Некорректная LIVE-сессия"})
            return
        supplied = str(payload.get("token", "")) if isinstance(payload, dict) else ""
        if not supplied or not hmac.compare_digest(supplied, self.session_token):
            self._json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "Недействительная локальная LIVE-сессия"})
            return
        self._json(
            HTTPStatus.OK,
            {"ok": True, "data": {"authenticated": True}},
            session_cookie=self.session_token,
        )


def main() -> int:
    lock: LiveProcessLock | None = None
    engine: SafeCompatibleGalkaLiveEngine | None = None
    server: ThreadingHTTPServer | None = None
    pid_file: Path | None = None
    try:
        config = load_config()
        runtime_dir = config.data_dir / "runtime"
        token_file = Path(
            os.environ.get("GALKA_LIVE_SESSION_TOKEN_FILE", runtime_dir / "browser-session.token")
        ).expanduser()
        pid_file = Path(os.environ.get("GALKA_LIVE_PID_FILE", runtime_dir / "server.pid")).expanduser()
        token = load_or_create_session_token(token_file)

        lock = LiveProcessLock(config.data_dir)
        lock.acquire()
        gateway = SafeCompatibleHyperliquidGateway(config)
        engine = SafeCompatibleGalkaLiveEngine(config, gateway)
        PersistentGalkaRequestHandler.engine = engine
        PersistentGalkaRequestHandler.session_token = token
        PersistentGalkaRequestHandler.server_port = config.port
        PersistentGalkaRequestHandler.cookie_name = f"GalkaLiveSession{config.port}"
        server = ThreadingHTTPServer((config.host, config.port), PersistentGalkaRequestHandler)
        server.daemon_threads = True
        write_pid_file(pid_file)
        engine.start()
    except (ConfigError, RuntimeError, GatewayError, LiveEngineError, OSError) as exc:
        if engine is not None:
            engine.stop()
        if server is not None:
            server.server_close()
        if lock is not None:
            lock.release()
        print(f"Galka LIVE не запущена: {exc}", file=sys.stderr, flush=True)
        return 2

    def stop_server(_signum: int, _frame: object) -> None:
        if server is not None:
            threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop_server)
    signal.signal(signal.SIGINT, stop_server)

    base_url = f"http://{config.host}:{config.port}/terminal/live.html"
    print(f"Galka LIVE: {base_url}", flush=True)
    print(f"Сеть: {config.network_name} · аккаунт {config.masked_address}", flush=True)
    print(f"Режим: {'LIVE ENABLED' if config.live_enabled else 'READ ONLY'}", flush=True)
    print(
        f"Плечо: {config.leverage}x isolated · номинал одной GALKA: ${config.total_notional:.2f}",
        flush=True,
    )
    print("Секретный ключ загружен из локального файла и не передаётся браузеру.", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        engine.stop()
        server.server_close()
        lock.release()
        if pid_file is not None:
            try:
                if pid_file.read_text(encoding="utf-8").strip() == str(os.getpid()):
                    pid_file.unlink()
            except (FileNotFoundError, OSError):
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
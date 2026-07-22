from __future__ import annotations

import fcntl
import hmac
import json
import os
import secrets
import sys
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .config import ConfigError, load_config
from .engine import LiveEngineError
from .hyperliquid_compat import CompatibleGalkaLiveEngine, CompatibleHyperliquidGateway
from .hyperliquid_gateway import GatewayError

REPO_ROOT = Path(__file__).resolve().parents[1]
TERMINAL_ROOT = REPO_ROOT / "terminal"


class LiveProcessLock:
    """Hold a non-blocking OS lock for the lifetime of one LIVE server."""

    def __init__(self, data_dir: Path):
        self.path = data_dir / "server.lock"
        self._descriptor: int | None = None

    def acquire(self) -> None:
        if self._descriptor is not None:
            return
        if self.path.is_symlink():
            raise LiveEngineError("LIVE process lock must not be a symlink")
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self.path, flags, 0o600)
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            try:
                os.close(descriptor)
            except (OSError, UnboundLocalError):
                pass
            raise LiveEngineError(
                "Another Galka LIVE server already owns this state directory"
            ) from exc
        except OSError as exc:
            try:
                os.close(descriptor)
            except (OSError, UnboundLocalError):
                pass
            raise LiveEngineError("Cannot securely create the LIVE process lock") from exc
        os.ftruncate(descriptor, 0)
        os.write(descriptor, f"{os.getpid()}\n".encode("ascii"))
        os.fsync(descriptor)
        self._descriptor = descriptor

    def release(self) -> None:
        descriptor, self._descriptor = self._descriptor, None
        if descriptor is None:
            return
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


class GalkaRequestHandler(SimpleHTTPRequestHandler):
    engine: CompatibleGalkaLiveEngine
    session_token: str
    server_port: int

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(TERMINAL_ROOT), **kwargs)

    def log_message(self, fmt: str, *args) -> None:
        message = fmt % args
        if self.path.startswith("/api/live/status"):
            return
        sys.stdout.write(f"[{self.log_date_time_string()}] {message}\n")
        sys.stdout.flush()

    def end_headers(self) -> None:
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
        )
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _json(self, status: int, payload: dict | list) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _allowed_origins(self) -> set[str]:
        return {
            f"http://127.0.0.1:{self.server_port}",
            f"http://localhost:{self.server_port}",
        }

    def _authorized_api_request(self) -> bool:
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
        if fetch_site and fetch_site not in {"same-origin", "none"}:
            return False
        supplied = self.headers.get("X-Galka-Session") or ""
        return hmac.compare_digest(supplied, self.session_token)

    def _require_api_auth(self) -> bool:
        if self._authorized_api_request():
            return True
        self._json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "Недействительная локальная LIVE-сессия"})
        return False

    def _read_json(self) -> dict:
        content_type = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise LiveEngineError("Ожидается Content-Type: application/json")
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError as exc:
            raise LiveEngineError("Некорректный Content-Length") from exc
        if length <= 0 or length > 64_000:
            raise LiveEngineError("Некорректное тело запроса")
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LiveEngineError("Некорректный JSON") from exc
        if not isinstance(data, dict):
            raise LiveEngineError("Ожидается JSON-объект")
        return data

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            if not self._require_api_auth():
                return
            if parsed.path == "/api/live/status":
                return self._handle(lambda: self.engine.status())
            if parsed.path == "/api/live/candles":
                query = parse_qs(parsed.query)
                coin = query.get("coin", [""])[0]
                interval = query.get("interval", ["15m"])[0]
                try:
                    limit = int(query.get("limit", ["1000"])[0])
                except ValueError:
                    return self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Некорректный limit"})
                return self._handle(lambda: self.engine.candles(coin, interval, limit))
            return self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "API endpoint not found"})

        if parsed.path == "/":
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", "/terminal/live.html")
            self.end_headers()
            return
        if not parsed.path.startswith("/terminal/"):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        # The static file root is terminal/. Strip the public /terminal prefix.
        original = self.path
        suffix = original[len("/terminal"):]
        self.path = suffix or "/live.html"
        try:
            return super().do_GET()
        finally:
            self.path = original

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/"):
            return self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "API endpoint not found"})
        if not self._require_api_auth():
            return
        try:
            data = self._read_json()
        except LiveEngineError as exc:
            return self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})

        if parsed.path == "/api/live/preview":
            return self._handle(
                lambda: self.engine.preview(str(data.get("coin", "")), float(data.get("galkaPrice", 0)))
            )
        if parsed.path == "/api/live/campaign":
            return self._handle(
                lambda: self.engine.create_campaign(
                    str(data.get("coin", "")),
                    float(data.get("galkaPrice", 0)),
                    str(data.get("confirmation", "")),
                )
            )
        if parsed.path == "/api/live/cancel":
            return self._handle(lambda: self.engine.cancel_waiting_campaign(str(data.get("coin", ""))))
        if parsed.path == "/api/live/emergency":
            return self._handle(
                lambda: self.engine.emergency_close(
                    str(data.get("coin", "")),
                    str(data.get("confirmation", "")),
                )
            )
        if parsed.path == "/api/live/reconcile":
            return self._handle(
                lambda: self.engine.reconcile_system(str(data.get("confirmation", "")))
            )
        return self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "API endpoint not found"})

    def _handle(self, action) -> None:
        try:
            result = action()
            self._json(HTTPStatus.OK, {"ok": True, "data": result})
        except (LiveEngineError, GatewayError, ValueError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
        except Exception as exc:  # do not expose secrets or tracebacks to the browser
            sys.stderr.write(f"LIVE API error: {type(exc).__name__}\n")
            sys.stderr.flush()
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": "Внутренняя ошибка LIVE-сервера"})


def main() -> int:
    lock: LiveProcessLock | None = None
    engine: CompatibleGalkaLiveEngine | None = None
    server: ThreadingHTTPServer | None = None
    try:
        config = load_config()
        lock = LiveProcessLock(config.data_dir)
        lock.acquire()
        gateway = CompatibleHyperliquidGateway(config)
        engine = CompatibleGalkaLiveEngine(config, gateway)
        token = secrets.token_urlsafe(32)
        GalkaRequestHandler.engine = engine
        GalkaRequestHandler.session_token = token
        GalkaRequestHandler.server_port = config.port
        server = ThreadingHTTPServer((config.host, config.port), GalkaRequestHandler)
        server.daemon_threads = True
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

    base_url = f"http://{config.host}:{config.port}/terminal/live.html"
    session_url = f"{base_url}#token={token}"
    print(f"Galka LIVE: {base_url}", flush=True)
    print(f"Galka LIVE URL: {session_url}", flush=True)
    print(f"Сеть: {config.network_name} · аккаунт {config.masked_address}", flush=True)
    print(f"Режим: {'LIVE ENABLED' if config.live_enabled else 'READ ONLY'}", flush=True)
    print(
        f"Плечо: {config.leverage}x isolated · номинал одной GALKA: ${config.total_notional:.2f}",
        flush=True,
    )
    print("Секретный ключ загружен из локального файла и не передаётся браузеру.", flush=True)
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

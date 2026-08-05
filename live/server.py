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

from .app_read_only import (
    MAX_CANDLE_LIMIT,
    MAX_EVENT_LIMIT,
    SUPPORTED_COINS,
    SUPPORTED_INTERVALS,
    SlidingWindowRateLimiter,
    build_snapshot,
    normalize_candles,
    parse_timestamp,
    sanitize_events,
)
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
    app_read_only_token: str | None = None
    app_allowed_origin: str | None = None
    app_rate_limiter = SlidingWindowRateLimiter()
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
        if not self.path.startswith("/api/app/"):
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

    def _app_origin_allowed(self) -> bool:
        origin = self.headers.get("Origin")
        return not origin or origin in self._allowed_origins() or origin == self.app_allowed_origin

    def _require_app_read_only_auth(self) -> bool:
        token = self.app_read_only_token
        supplied = self.headers.get("X-Galka-App-Token") or ""
        if token and self._app_origin_allowed() and hmac.compare_digest(supplied, token):
            return True
        self._json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "Invalid read-only app credentials"})
        return False

    def _app_cors_headers(self) -> None:
        origin = self.headers.get("Origin")
        if origin and self._app_origin_allowed():
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    def _app_json(self, status: int, payload: dict | list) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._app_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _app_error(self, status: int, message: str) -> None:
        self._app_json(status, {"ok": False, "error": message})

    def _app_rate_allowed(self, route: str, maximum: int) -> bool:
        client = self.client_address[0] if self.client_address else "local"
        if self.app_rate_limiter.allow(client, route, maximum):
            return True
        self._app_error(HTTPStatus.TOO_MANY_REQUESTS, "Read-only API rate limit exceeded")
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
        if parsed.path.startswith("/api/app/"):
            return self._handle_app_get(parsed)
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
        if parsed.path.startswith("/api/app/"):
            return self._app_error(HTTPStatus.METHOD_NOT_ALLOWED, "Read-only API accepts GET only")
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

    def do_PUT(self) -> None:  # noqa: N802
        self._reject_app_mutation()

    def do_PATCH(self) -> None:  # noqa: N802
        self._reject_app_mutation()

    def do_DELETE(self) -> None:  # noqa: N802
        self._reject_app_mutation()

    def do_OPTIONS(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/app/") or not self._app_origin_allowed():
            return self._app_error(HTTPStatus.FORBIDDEN, "Origin is not allowed")
        self.send_response(HTTPStatus.NO_CONTENT)
        self._app_cors_headers()
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "X-Galka-App-Token")
        self.send_header("Access-Control-Max-Age", "600")
        self.end_headers()

    def _reject_app_mutation(self) -> None:
        if urlparse(self.path).path.startswith("/api/app/"):
            return self._app_error(HTTPStatus.METHOD_NOT_ALLOWED, "Read-only API accepts GET only")
        return self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "API endpoint not found"})

    def _handle_app_get(self, parsed) -> None:
        if parsed.path not in {"/api/app/snapshot", "/api/app/candles", "/api/app/events"}:
            return self._app_error(HTTPStatus.NOT_FOUND, "Read-only endpoint not found")
        if not self._require_app_read_only_auth():
            return
        limits = {"/api/app/snapshot": 5, "/api/app/candles": 2, "/api/app/events": 5}
        if not self._app_rate_allowed(parsed.path, limits[parsed.path]):
            return
        try:
            query = parse_qs(parsed.query)
            if parsed.path == "/api/app/snapshot":
                status = self.engine.status()
                orders = self.engine.gateway.open_orders()
                return self._app_json(HTTPStatus.OK, {"ok": True, "data": build_snapshot(status, orders)})
            if parsed.path == "/api/app/candles":
                coin = query.get("coin", [""])[0].upper()
                interval = query.get("interval", [""])[0]
                if coin not in SUPPORTED_COINS or interval not in SUPPORTED_INTERVALS:
                    return self._app_error(HTTPStatus.BAD_REQUEST, "Invalid coin or interval")
                limit = int(query.get("limit", ["300"])[0])
                if limit < 1:
                    raise ValueError("invalid limit")
                limit = min(limit, MAX_CANDLE_LIMIT)
                from_ms = parse_timestamp(query.get("from", [None])[0])
                to_ms = parse_timestamp(query.get("to", [None])[0])
                rows = self.engine.candles(coin, interval, limit)
                data = normalize_candles(rows, from_ms=from_ms, to_ms=to_ms)[-limit:]
                return self._app_json(HTTPStatus.OK, {"ok": True, "data": data})
            limit = int(query.get("limit", ["50"])[0])
            if limit < 1:
                raise ValueError("invalid limit")
            limit = min(limit, MAX_EVENT_LIMIT)
            since = query.get("since", [None])[0]
            events = sanitize_events(self.engine.status().get("events") or [])
            if since:
                matching = next((index for index, event in enumerate(events) if event["id"] == since), None)
                if matching is not None:
                    events = events[matching + 1:]
                else:
                    since_ms = parse_timestamp(since)
                    events = [event for event in events if parse_timestamp(str(event.get("timestamp") or "")) and parse_timestamp(str(event.get("timestamp") or "")) > since_ms]
            return self._app_json(HTTPStatus.OK, {"ok": True, "data": events[-limit:]})
        except (TypeError, ValueError):
            return self._app_error(HTTPStatus.BAD_REQUEST, "Invalid read-only request parameters")
        except Exception as exc:
            sys.stderr.write(f"App read-only API error: {type(exc).__name__}\n")
            sys.stderr.flush()
            return self._app_error(HTTPStatus.INTERNAL_SERVER_ERROR, "Read-only API unavailable")

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
        GalkaRequestHandler.app_read_only_token = config.app_read_only_token
        GalkaRequestHandler.app_allowed_origin = config.app_allowed_origin
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

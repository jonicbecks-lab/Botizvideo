#!/usr/bin/env python3
"""Loopback-only Touch Lab server with a persistent HttpOnly session cookie."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import signal
import stat
import time
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

COOKIE_NAME = "GalkaTouchLabSession8099"
COOKIE_MAX_AGE = 30 * 24 * 60 * 60
MAX_BODY_BYTES = 4096


def read_private_token(path: Path) -> str:
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise RuntimeError("Touch Lab token file must be a regular file")
    token = path.read_text(encoding="utf-8").strip()
    if len(token) < 32:
        raise RuntimeError("Touch Lab token is missing or too short")
    return token


class TouchLabServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], handler: type[SimpleHTTPRequestHandler], *, token: str):
        super().__init__(address, handler)
        self.session_token = token
        self.started_at = time.time()


class TouchLabHandler(SimpleHTTPRequestHandler):
    server: TouchLabServer

    def __init__(self, *args: Any, directory: str, **kwargs: Any) -> None:
        super().__init__(*args, directory=directory, **kwargs)

    def log_message(self, format_string: str, *args: Any) -> None:
        # The bootstrap token lives only in the URL fragment, which is never sent
        # to the server. Keep logs compact and avoid printing request headers.
        super().log_message(format_string, *args)

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        if self.path.startswith("/touch-lab") or self.path == "/healthz":
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _json(self, status: HTTPStatus, payload: dict[str, Any], *, cookie: str | None = None) -> None:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if cookie is not None:
            self.send_header(
                "Set-Cookie",
                f"{COOKIE_NAME}={cookie}; HttpOnly; SameSite=Strict; Path=/; Max-Age={COOKIE_MAX_AGE}",
            )
        self.end_headers()
        self.wfile.write(body)

    def _session_cookie(self) -> str:
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except Exception:
            return ""
        morsel = cookie.get(COOKIE_NAME)
        return morsel.value if morsel is not None else ""

    def _authenticated(self) -> bool:
        supplied = self._session_cookie()
        return bool(supplied) and secrets.compare_digest(supplied, self.server.session_token)

    def _status_payload(self) -> dict[str, Any]:
        return {
            "ok": True,
            "server": "touch-lab",
            "authenticated": True,
            "pid": os.getpid(),
            "startedAt": int(self.server.started_at),
            "uptimeSeconds": max(0, int(time.time() - self.server.started_at)),
        }

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        if self.path == "/":
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", "/touch-lab.html")
            self.end_headers()
            return
        if self.path == "/healthz":
            self._json(HTTPStatus.OK, {"ok": True, "server": "touch-lab", "pid": os.getpid()})
            return
        if self.path == "/touch-lab/status":
            if not self._authenticated():
                self._json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "NO_SESSION"})
                return
            self._json(HTTPStatus.OK, self._status_payload())
            return
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        if self.path != "/touch-lab/session":
            self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "NOT_FOUND"})
            return
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            content_length = 0
        if content_length <= 0 or content_length > MAX_BODY_BYTES:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "BAD_BODY"})
            return
        try:
            payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "BAD_JSON"})
            return
        supplied = str(payload.get("token", ""))
        if not supplied or not secrets.compare_digest(supplied, self.server.session_token):
            self._json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "BAD_TOKEN"})
            return
        self._json(HTTPStatus.OK, self._status_payload(), cookie=self.server.session_token)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--token-file", required=True)
    parser.add_argument("--pid-file", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8099)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.host != "127.0.0.1":
        raise RuntimeError("Touch Lab must bind only to 127.0.0.1")
    root = Path(args.root).resolve(strict=True)
    token_file = Path(args.token_file).expanduser()
    pid_file = Path(args.pid_file).expanduser()
    token = read_private_token(token_file)
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(f"{os.getpid()}\n", encoding="utf-8")
    os.chmod(pid_file, 0o600)

    def handler(*handler_args: Any, **handler_kwargs: Any) -> TouchLabHandler:
        return TouchLabHandler(*handler_args, directory=str(root), **handler_kwargs)

    server = TouchLabServer((args.host, args.port), handler, token=token)

    def stop_server(_signum: int, _frame: Any) -> None:
        # shutdown() must run outside the request/signal thread.
        import threading

        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop_server)
    signal.signal(signal.SIGINT, stop_server)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
        try:
            if pid_file.read_text(encoding="utf-8").strip() == str(os.getpid()):
                pid_file.unlink()
        except (FileNotFoundError, OSError):
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

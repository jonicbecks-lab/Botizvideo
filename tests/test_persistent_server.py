from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from live.engine import LiveEngineError
from live.persistent_server import (
    PersistentGalkaRequestHandler,
    load_or_create_session_token,
    write_pid_file,
)


class _DummyEngine:
    def status(self) -> dict:
        return {"liveEnabled": True, "safeMode": False}


class PersistentServerSecurityTests(unittest.TestCase):
    def test_session_token_is_reused_and_private(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            token_path = Path(temporary) / "runtime" / "browser-session.token"
            first = load_or_create_session_token(token_path)
            second = load_or_create_session_token(token_path)
            self.assertEqual(first, second)
            self.assertGreaterEqual(len(first), 32)
            self.assertEqual(token_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(token_path.parent.stat().st_mode & 0o777, 0o700)

    def test_session_token_rejects_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            target.write_text("not-secret\n", encoding="utf-8")
            token_path = root / "browser-session.token"
            token_path.symlink_to(target)
            with self.assertRaises(LiveEngineError):
                load_or_create_session_token(token_path)

    def test_pid_file_is_atomic_and_private(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pid_path = Path(temporary) / "runtime" / "server.pid"
            write_pid_file(pid_path)
            self.assertEqual(pid_path.read_text(encoding="utf-8").strip(), str(os.getpid()))
            self.assertEqual(pid_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(pid_path.parent.stat().st_mode & 0o777, 0o700)

    def test_cookie_bootstrap_survives_without_legacy_header(self) -> None:
        token = "t" * 64
        PersistentGalkaRequestHandler.session_token = token
        PersistentGalkaRequestHandler.engine = _DummyEngine()
        server = ThreadingHTTPServer(("127.0.0.1", 0), PersistentGalkaRequestHandler)
        server.daemon_threads = True
        port = int(server.server_address[1])
        PersistentGalkaRequestHandler.server_port = port
        PersistentGalkaRequestHandler.cookie_name = f"GalkaLiveSession{port}"
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            origin = f"http://127.0.0.1:{port}"
            body = json.dumps({"token": token}).encode("utf-8")
            bootstrap = urllib.request.Request(
                f"{origin}/api/live/session",
                data=body,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "Origin": origin,
                    "Sec-Fetch-Site": "same-origin",
                },
            )
            with urllib.request.urlopen(bootstrap, timeout=2) as response:
                payload = json.load(response)
                cookie = response.headers.get("Set-Cookie", "")
            self.assertTrue(payload["ok"])
            self.assertIn("HttpOnly", cookie)
            self.assertIn("SameSite=Strict", cookie)
            cookie_pair = cookie.split(";", 1)[0]

            status_request = urllib.request.Request(
                f"{origin}/api/live/status",
                headers={
                    "Cookie": cookie_pair,
                    "Origin": origin,
                    "Sec-Fetch-Site": "same-origin",
                },
            )
            with urllib.request.urlopen(status_request, timeout=2) as response:
                status_payload = json.load(response)
            self.assertTrue(status_payload["ok"])
            self.assertTrue(status_payload["data"]["liveEnabled"])

            unauthenticated = urllib.request.Request(
                f"{origin}/api/live/status",
                headers={"Origin": origin, "Sec-Fetch-Site": "same-origin"},
            )
            with self.assertRaises(urllib.error.HTTPError) as captured:
                urllib.request.urlopen(unauthenticated, timeout=2)
            self.assertEqual(captured.exception.code, 401)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()

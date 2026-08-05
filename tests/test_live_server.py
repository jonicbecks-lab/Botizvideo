from __future__ import annotations

import http.client
import json
import stat
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from live.engine import LiveEngineError
from live.app_read_only import SlidingWindowRateLimiter
from live.server import GalkaRequestHandler, LiveProcessLock


class DummyGateway:
    def __init__(self):
        self.trade_calls = 0

    def open_orders(self):
        return [
            {"coin": "BTC", "oid": 11, "price": 100.0, "size": 0.1, "reduceOnly": False},
            {"coin": "BTC", "oid": 12, "price": 110.0, "size": 0.1, "reduceOnly": True, "isTrigger": True},
        ]


class DummyEngine:
    def __init__(self):
        self.gateway = DummyGateway()
        self.mutation_calls = 0

    def status(self):
        return {
            "ready": True,
            "liveEnabled": True,
            "serverTime": 1_700_000_000_000,
            "system": {"safeMode": False, "lastReconcileAt": "2026-08-05T00:00:00Z"},
            "accountState": {
                "accountValue": 1000,
                "withdrawable": 800,
                "totalMarginUsed": 200,
                "positions": {"BTC": {"size": 0.1, "entryPrice": 100, "marginUsed": 10, "unrealizedPnl": 2}},
            },
            "mids": {"BTC": 101, "ETH": 3000, "SOL": 150},
            "leverage": 10,
            "campaigns": {"BTC": {"id": "campaign-1", "galkaPrice": 110, "leverage": 10, "levels": [
                {"index": index, "price": 100 - index, "filledSize": 0.01 if index == 1 else 0, "status": "filled" if index == 1 else "resting"}
                for index in range(1, 9)
            ]}},
            "events": [{"time": "2026-08-05T00:00:00Z", "type": "live", "message": "Сверка успешна", "meta": {"secret": "must-not-leak"}}],
        }

    def candles(self, coin, interval, limit):
        return [
            {"openTime": 2_000, "open": 2, "high": 3, "low": 1, "close": 2.5, "volume": 4},
            {"openTime": 1_000, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 3},
            {"openTime": 1_000, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 3},
            {"openTime": 3_000, "open": "broken", "high": 2, "low": 1, "close": 1.5},
        ]

    def reconcile_system(self, confirmation):
        self.mutation_calls += 1
        return {"confirmation": confirmation}


class LiveServerSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        GalkaRequestHandler.engine = DummyEngine()
        GalkaRequestHandler.session_token = "test-session-token"
        GalkaRequestHandler.app_read_only_token = "r" * 40
        GalkaRequestHandler.app_allowed_origin = None
        GalkaRequestHandler.app_rate_limiter = SlidingWindowRateLimiter()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), GalkaRequestHandler)
        cls.server.daemon_threads = True
        GalkaRequestHandler.server_port = cls.server.server_port
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self):
        GalkaRequestHandler.app_rate_limiter = SlidingWindowRateLimiter()
        GalkaRequestHandler.engine.mutation_calls = 0

    def request(self, method, path, headers=None, body=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        payload = response.read()
        result = (response.status, dict(response.getheaders()), payload)
        connection.close()
        return result

    def auth_headers(self):
        return {
            "Host": f"127.0.0.1:{self.server.server_port}",
            "Origin": f"http://127.0.0.1:{self.server.server_port}",
            "Sec-Fetch-Site": "same-origin",
            "X-Galka-Session": "test-session-token",
        }

    def app_headers(self, token=None):
        headers = {"Host": f"127.0.0.1:{self.server.server_port}"}
        if token is not None:
            headers["X-Galka-App-Token"] = token
        return headers

    def test_read_only_token_gets_snapshot_without_sensitive_data(self):
        status, _, body = self.request("GET", "/api/app/snapshot", headers=self.app_headers("r" * 40))
        self.assertEqual(status, 200)
        payload = json.loads(body)["data"]
        self.assertEqual(payload["account"]["balance"], 1000)
        self.assertEqual(payload["markets"]["BTC"]["levels"][7]["index"], 8)
        self.assertEqual(payload["markets"]["BTC"]["activeReduceOnlyOrders"][0]["oid"], 12)
        serialized = json.dumps(payload).lower()
        for forbidden in ("private", "seed", "signing", "must-not-leak"):
            self.assertNotIn(forbidden, serialized)

    def test_read_only_api_rejects_missing_and_invalid_tokens(self):
        self.assertEqual(self.request("GET", "/api/app/snapshot", headers=self.app_headers())[0], 401)
        self.assertEqual(self.request("GET", "/api/app/snapshot", headers=self.app_headers("wrong"))[0], 401)

    def test_read_only_token_cannot_authorize_live_post(self):
        body = json.dumps({"confirmation": "RECONCILE_LOCAL_STATE"}).encode()
        headers = {**self.app_headers("r" * 40), "Content-Type": "application/json", "Content-Length": str(len(body))}
        status, _, _ = self.request("POST", "/api/live/reconcile", headers=headers, body=body)
        self.assertEqual(status, 401)
        self.assertEqual(GalkaRequestHandler.engine.mutation_calls, 0)

    def test_all_app_mutations_are_method_not_allowed(self):
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            with self.subTest(method=method):
                self.assertEqual(self.request(method, "/api/app/snapshot", headers=self.app_headers("r" * 40))[0], 405)

    def test_candles_validate_and_normalize(self):
        headers = self.app_headers("r" * 40)
        self.assertEqual(self.request("GET", "/api/app/candles?coin=XRP&interval=1m", headers=headers)[0], 400)
        self.assertEqual(self.request("GET", "/api/app/candles?coin=BTC&interval=2m", headers=headers)[0], 400)
        GalkaRequestHandler.app_rate_limiter = SlidingWindowRateLimiter()
        status, _, body = self.request("GET", "/api/app/candles?coin=BTC&interval=1m&limit=5000", headers=headers)
        self.assertEqual(status, 200)
        data = json.loads(body)["data"]
        self.assertEqual([row["timestamp"] for row in data], [1000, 2000])

    def test_events_bound_limit_and_strip_meta(self):
        status, _, body = self.request("GET", "/api/app/events?limit=9999", headers=self.app_headers("r" * 40))
        self.assertEqual(status, 200)
        self.assertNotIn("meta", json.loads(body)["data"][0])

    def test_app_rate_limit_returns_429_without_engine_mutation(self):
        GalkaRequestHandler.app_rate_limiter = SlidingWindowRateLimiter()
        statuses = [self.request("GET", "/api/app/snapshot", headers=self.app_headers("r" * 40))[0] for _ in range(6)]
        self.assertEqual(statuses[-1], 429)
        self.assertEqual(GalkaRequestHandler.engine.mutation_calls, 0)
        GalkaRequestHandler.app_rate_limiter = SlidingWindowRateLimiter()

    def test_api_rejects_missing_session_token(self):
        status, _, _ = self.request("GET", "/api/live/status")
        self.assertEqual(status, 401)

    def test_api_rejects_cross_origin_request(self):
        headers = self.auth_headers()
        headers["Origin"] = "https://evil.example"
        status, _, _ = self.request("GET", "/api/live/status", headers=headers)
        self.assertEqual(status, 401)

    def test_authenticated_same_origin_request_succeeds(self):
        status, headers, body = self.request("GET", "/api/live/status", headers=self.auth_headers())
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["data"]["ready"])
        self.assertIn("default-src 'self'", headers["Content-Security-Policy"])

    def test_static_server_does_not_expose_repository_files(self):
        status, _, _ = self.request("GET", "/live/engine.py")
        self.assertEqual(status, 404)
        status, _, _ = self.request("GET", "/terminal/live.html")
        self.assertEqual(status, 200)

    def test_post_requires_json_and_session(self):
        headers = self.auth_headers()
        headers["Content-Type"] = "text/plain"
        status, _, _ = self.request("POST", "/api/live/reconcile", headers=headers, body=b"{}")
        self.assertEqual(status, 400)

        headers["Content-Type"] = "application/json"
        body = json.dumps({"confirmation": "RECONCILE_LOCAL_STATE"}).encode()
        headers["Content-Length"] = str(len(body))
        status, _, payload = self.request("POST", "/api/live/reconcile", headers=headers, body=body)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload)["data"]["confirmation"], "RECONCILE_LOCAL_STATE")

    def test_process_lock_prevents_two_servers_sharing_state(self):
        with tempfile.TemporaryDirectory() as directory:
            first = LiveProcessLock(Path(directory))
            second = LiveProcessLock(Path(directory))
            first.acquire()
            self.addCleanup(first.release)
            self.assertEqual(stat.S_IMODE(first.path.stat().st_mode), 0o600)
            with self.assertRaisesRegex(LiveEngineError, "already owns"):
                second.acquire()
            first.release()
            second.acquire()
            second.release()

    def test_process_lock_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target"
            target.write_text("do not overwrite\n", encoding="utf-8")
            (root / "server.lock").symlink_to(target)
            with self.assertRaisesRegex(LiveEngineError, "symlink"):
                LiveProcessLock(root).acquire()
            self.assertEqual(target.read_text(encoding="utf-8"), "do not overwrite\n")


if __name__ == "__main__":
    unittest.main()

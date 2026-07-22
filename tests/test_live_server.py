from __future__ import annotations

import http.client
import json
import threading
import unittest
from http.server import ThreadingHTTPServer

from live.server import GalkaRequestHandler


class DummyEngine:
    def status(self):
        return {"ready": True}

    def candles(self, coin, interval, limit):
        return [{"coin": coin, "interval": interval, "limit": limit}]

    def reconcile_system(self, confirmation):
        return {"confirmation": confirmation}


class LiveServerSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        GalkaRequestHandler.engine = DummyEngine()
        GalkaRequestHandler.session_token = "test-session-token"
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
        self.assertEqual(json.loads(body)["data"], {"ready": True})
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


if __name__ == "__main__":
    unittest.main()

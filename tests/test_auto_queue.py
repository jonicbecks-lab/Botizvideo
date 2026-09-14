from __future__ import annotations

import threading
import time
import unittest
from types import SimpleNamespace

from live.auto_queue_engine import AutoQueueGalkaLiveEngine
from live.engine import LiveEngineError


class FakeGateway:
    def __init__(self, mid: float = 100.0, candles: list[dict] | None = None):
        self.mid = mid
        self.rows = candles or []
        self.cached = {"BTC": mid, "ETH": 200.0, "SOL": 10.0}

    def mids(self):
        return {"BTC": self.mid, "ETH": 200.0, "SOL": 10.0}

    def candles(self, coin: str, interval: str, limit: int):
        return list(self.rows)

    def _cache_get(self, key: str, ttl: float):
        return dict(self.cached) if key == "mids" else None


class AutoQueueTests(unittest.TestCase):
    def make_engine(self, *, source_status: str = "waiting", mid: float = 100.0):
        engine = AutoQueueGalkaLiveEngine.__new__(AutoQueueGalkaLiveEngine)
        engine.config = SimpleNamespace(live_enabled=True)
        engine.gateway = FakeGateway(mid=mid)
        engine.lock = threading.RLock()
        engine.state = {
            "system": {"safeMode": False, "safeModeReason": None},
            "campaigns": {
                "BTC": {
                    "id": "source-campaign",
                    "coin": "BTC",
                    "status": source_status,
                    "levels": [],
                }
            },
            "queuedGalkas": {},
            "events": [],
        }
        engine._save_locked = lambda: None
        return engine

    def test_queue_requires_level_still_above_current_touch(self):
        engine = self.make_engine(mid=100.0)
        with self.assertRaises(LiveEngineError):
            engine.queue_next_galka("BTC", 100.0, "QUEUE_REAL_GALKA")
        self.assertEqual(engine.state["queuedGalkas"], {})

    def test_queue_is_persisted_against_source_campaign_and_can_be_replaced(self):
        engine = self.make_engine(mid=100.0)
        first = engine.queue_next_galka("BTC", 95.0, "QUEUE_REAL_GALKA")
        second = engine.queue_next_galka("BTC", 94.0, "QUEUE_REAL_GALKA")
        self.assertEqual(first["sourceCampaignId"], "source-campaign")
        self.assertEqual(second["sourceCampaignId"], "source-campaign")
        self.assertEqual(second["galkaPrice"], 94.0)
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(engine.state["queuedGalkas"]["BTC"]["status"], "queued")

    def test_history_low_invalidates_queued_level(self):
        engine = self.make_engine(mid=101.0)
        queued_ms = int(time.time() * 1000) - 30_000
        engine.gateway.rows = [
            {
                "openTime": queued_ms - 5_000,
                "closeTime": queued_ms + 55_000,
                "low": 94.5,
            }
        ]
        row = {
            "coin": "BTC",
            "galkaPrice": 95.0,
            "queuedMs": queued_ms,
        }
        check = engine._history_touch_check(row)
        self.assertTrue(check["touched"])
        self.assertEqual(check["basis"], "1m_candle_low")
        self.assertEqual(check["price"], 94.5)

    def test_paused_queue_manual_activation_rechecks_history_then_uses_normal_create(self):
        engine = self.make_engine(source_status="canceled", mid=101.0)
        queued_ms = int(time.time() * 1000) - 30_000
        engine.gateway.rows = [
            {
                "openTime": queued_ms - 5_000,
                "closeTime": queued_ms + 55_000,
                "low": 96.0,
            }
        ]
        engine.state["queuedGalkas"]["BTC"] = {
            "id": "queue-1",
            "coin": "BTC",
            "galkaPrice": 95.0,
            "status": "paused",
            "queuedAt": "2026-08-15T00:00:00Z",
            "queuedMs": queued_ms,
            "sourceCampaignId": "source-campaign",
            "pausedReason": "manual cancel",
        }
        calls = []

        def fake_create(coin: str, price: float, confirmation: str):
            calls.append((coin, price, confirmation))
            engine.state["campaigns"][coin] = {
                "id": "new-campaign",
                "coin": coin,
                "status": "waiting",
                "levels": [],
            }
            return dict(engine.state["campaigns"][coin])

        engine.create_campaign = fake_create
        result = engine.activate_queued_galka("BTC", "ACTIVATE_QUEUED_GALKA")
        self.assertEqual(calls, [("BTC", 95.0, "PLACE_REAL_ORDERS")])
        self.assertEqual(result["queue"]["status"], "activated")
        self.assertEqual(result["queue"]["activatedCampaignId"], "new-campaign")


if __name__ == "__main__":
    unittest.main()

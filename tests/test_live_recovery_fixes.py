from __future__ import annotations

import threading
import unittest
from unittest.mock import patch

from live.live_recovery_fixes import ReliableGalkaLiveEngine


class _Gateway:
    def __init__(self) -> None:
        self.position = 0.00091
        self.orders: list[dict] = []
        self.fills: list[dict] = []

    def sz_decimals(self, _coin: str) -> int:
        return 5

    def fresh_open_orders(self, _coin: str | None = None) -> list[dict]:
        return [dict(row) for row in self.orders]

    def fresh_account_state(self) -> dict:
        return {"positions": {"BTC": {"size": self.position}}}

    def fills_since(self, _cursor: int) -> list[dict]:
        return [dict(row) for row in self.fills]


class RecoveryFixTests(unittest.TestCase):
    def _engine(self) -> ReliableGalkaLiveEngine:
        engine = ReliableGalkaLiveEngine.__new__(ReliableGalkaLiveEngine)
        engine.gateway = _Gateway()
        engine.lock = threading.RLock()
        engine.state = {
            "system": {"safeMode": False, "safeModeReason": None},
            "campaigns": {},
            "events": [],
        }
        engine._save_locked = lambda: None
        return engine

    def test_target_validation_uses_exchange_rounded_price(self) -> None:
        engine = self._engine()
        campaign = {"coin": "BTC", "galkaPrice": 64644.05}
        order = {
            "side": "A",
            "reduceOnly": True,
            "triggerPrice": 64644.0,
            "price": 64644.0,
        }
        self.assertTrue(engine._is_galka_target(campaign, order))

    def test_delayed_owned_fill_catches_up_before_recovery(self) -> None:
        engine = self._engine()
        campaign = {
            "id": "test-campaign",
            "coin": "BTC",
            "status": "open",
            "createdMs": 1_000,
            "fillCursorMs": 0,
            "updatedAt": None,
            "entryOidMap": {"44": 4},
            "targetOidMap": {},
            "entryCloidMap": {},
            "targetCloidMap": {},
            "levels": [
                {
                    "index": 4,
                    "size": 0.00016,
                    "filledSize": 0.0,
                    "averageFillPrice": 0.0,
                    "status": "resting",
                }
            ],
            "managedNetSize": 0.00075,
            "actualPositionSize": 0.00091,
            "cycleDeepest": 3,
            "cycleFees": 0.0,
            "cycleClosedPnl": 0.0,
            "seenFills": [],
            "unknownSeenFills": [],
            "hadPosition": True,
            "autoRearmBlocked": False,
            "lastError": "temporary mismatch",
        }
        engine.state["campaigns"]["BTC"] = campaign
        engine.gateway.fills = [
            {
                "coin": "BTC",
                "oid": 44,
                "cloid": None,
                "hash": "fill-44",
                "time": 2_000,
                "side": "B",
                "size": 0.00016,
                "price": 64256.0,
                "fee": 0.0,
                "closedPnl": 0.0,
            }
        ]

        with patch("live.live_recovery_fixes.time.sleep", return_value=None):
            resolved = engine._retry_delayed_owned_fills(campaign)

        self.assertTrue(resolved)
        self.assertAlmostEqual(campaign["managedNetSize"], 0.00091)
        self.assertEqual(campaign["cycleDeepest"], 4)
        self.assertEqual(campaign["levels"][0]["status"], "filled")
        self.assertIsNone(campaign["lastError"])

    def test_recovery_marks_disappeared_entries_canceled(self) -> None:
        engine = self._engine()
        campaign = {
            "coin": "BTC",
            "levels": [
                {"index": 1, "size": 0.00035, "filledSize": 0.00035, "status": "filled"},
                {"index": 5, "size": 0.00016, "filledSize": 0.0, "status": "resting"},
            ],
            "entryOidMap": {"11": 1, "55": 5},
            "entryCloidMap": {},
        }

        engine._mark_entry_statuses_after_recovery(campaign, [])

        self.assertEqual(campaign["levels"][0]["status"], "filled")
        self.assertEqual(campaign["levels"][1]["status"], "canceled")

    def test_network_error_does_not_replace_recovery_reason(self) -> None:
        engine = self._engine()
        campaign = {
            "coin": "BTC",
            "status": "recovery",
            "recoveryReason": "Расхождение позиции: биржа 0.00091, GALKA 0.00075",
        }
        engine.state["campaigns"]["BTC"] = campaign
        engine.state["system"].update(
            {
                "safeMode": True,
                "safeModeReason": "BTC recovery: Расхождение позиции",
            }
        )

        engine._set_safe_mode_locked(
            "BTC sync error: Hyperliquid read failed: Failed to resolve api.hyperliquid.xyz"
        )

        self.assertIn("Расхождение позиции", engine.state["system"]["safeModeReason"])
        self.assertIn("Hyperliquid read failed", engine.state["system"]["lastNetworkError"])


if __name__ == "__main__":
    unittest.main()

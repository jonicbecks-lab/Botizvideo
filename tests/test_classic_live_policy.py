from __future__ import annotations

import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from live.classic_live_policy import (
    CLASSIC_LEVERAGE,
    _classic_load_config,
    detect_classic_orphan_ladder,
    recover_flat_classic_orphans,
)
from live.live_ladder import build_ladder


def _orders(galka: float = 100_000.0, notional: float = 3000.0, sz_decimals: int = 5):
    levels = build_ladder(galka, notional, sz_decimals)
    return [
        {
            "coin": "BTC",
            "oid": 10_000 + level.index,
            "cloid": f"0x{level.index:032x}",
            "side": "B",
            "price": level.price,
            "size": level.size,
            "originalSize": level.size,
            "reduceOnly": False,
            "isTrigger": False,
            "timestamp": 1_790_000_000_000 + level.index * 1000,
        }
        for level in levels
    ]


class ClassicPolicyDetectorTests(unittest.TestCase):
    def test_detects_exact_classic_eight_level_ladder(self):
        match = detect_classic_orphan_ladder(_orders(), 5)
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(len(match["orders"]), 8)
        self.assertAlmostEqual(match["galkaPrice"], 100_000.0, delta=2.0)

    def test_rejects_partial_or_unrelated_order_set(self):
        partial = _orders()
        partial[0]["size"] *= 0.5
        self.assertIsNone(detect_classic_orphan_ladder(partial, 5))

        unrelated = _orders()
        unrelated[4]["price"] *= 0.995
        self.assertIsNone(detect_classic_orphan_ladder(unrelated, 5))

    def test_runtime_config_forces_production_leverage_without_editing_secret_config(self):
        original = SimpleNamespace(leverage=3)
        with patch("live.classic_live_policy._ORIGINAL_LOAD_CONFIG", return_value=original):
            result = _classic_load_config()
        self.assertIs(result, original)
        self.assertEqual(result.leverage, CLASSIC_LEVERAGE)


class _Gateway:
    def __init__(self, orders):
        self.orders = [dict(row) for row in orders]
        self.write_calls = []

    def fresh_account_state(self):
        return {"positions": {}}

    def fresh_open_orders(self):
        return [dict(row) for row in self.orders]

    @staticmethod
    def sz_decimals(_coin):
        return 5


class _RecoveryEngine:
    def __init__(self, orders):
        self.gateway = _Gateway(orders)
        self.lock = threading.RLock()
        self.config = SimpleNamespace(leverage=10, isolated=True)
        self.state = {
            "system": {
                "safeMode": True,
                "safeModeReason": "BTC: 8 orphan orders",
            },
            "campaigns": {},
            "events": [],
        }
        self.save_calls = 0

    @staticmethod
    def _position_size(account, coin):
        row = account.get("positions", {}).get(coin)
        return float(row.get("size") or 0.0) if row else 0.0

    @staticmethod
    def _size_tolerance(_coin):
        return 0.000005

    def _new_campaign(self, campaign_id, coin, galka_price, preview, levels):
        return {
            "id": campaign_id,
            "coin": coin,
            "status": "placing",
            "galkaPrice": galka_price,
            "leverage": self.config.leverage,
            "isolated": True,
            "requestedNotional": preview["requestedNotional"],
            "actualNotional": preview["actualNotional"],
            "levels": [
                {
                    **level.to_dict(),
                    "oid": None,
                    "tpOid": None,
                    "entryCloid": None,
                    "targetCloid": None,
                    "status": "new",
                    "filledSize": 0.0,
                    "averageFillPrice": 0.0,
                }
                for level in levels
            ],
            "entryOidMap": {},
            "targetOidMap": {},
            "entryCloidMap": {},
            "targetCloidMap": {},
        }

    def _event_locked(self, event_type, message, **meta):
        self.state["events"].append({"type": event_type, "message": message, "meta": meta})

    def _save_locked(self):
        self.save_calls += 1


class ClassicPolicyRecoveryTests(unittest.TestCase):
    def test_flat_matching_orphans_are_adopted_without_exchange_writes(self):
        engine = _RecoveryEngine(_orders())
        recovered = recover_flat_classic_orphans(engine)

        self.assertEqual(recovered, ["BTC"])
        campaign = engine.state["campaigns"]["BTC"]
        self.assertEqual(campaign["status"], "waiting")
        self.assertTrue(campaign["recoveredOrphanOrders"])
        self.assertEqual(len(campaign["levels"]), 8)
        self.assertEqual(
            [level["oid"] for level in campaign["levels"]],
            [10_001, 10_002, 10_003, 10_004, 10_005, 10_006, 10_007, 10_008],
        )
        self.assertFalse(engine.state["system"]["safeMode"])
        self.assertIsNone(engine.state["system"]["safeModeReason"])
        self.assertEqual(engine.gateway.write_calls, [])

    def test_ambiguous_orphans_remain_unowned_and_safe_mode_stays_on(self):
        orders = _orders()
        orders.pop()
        engine = _RecoveryEngine(orders)
        recovered = recover_flat_classic_orphans(engine)

        self.assertEqual(recovered, [])
        self.assertNotIn("BTC", engine.state["campaigns"])
        self.assertTrue(engine.state["system"]["safeMode"])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from live.config import LiveConfig
from live.galka_classic_engine import GalkaClassicEngine
from live.tpsl_batch_compat import install
from test_galka_classic_engine import ClassicFakeGateway


class TriggerChildFillReconciliationTests(unittest.TestCase):
    def setUp(self):
        install()
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.config = LiveConfig(
            account_address="0x" + "11" * 20,
            api_secret_key="0x" + "33" * 32,
            mainnet=True,
            live_enabled=True,
            leverage=10,
            isolated=True,
            total_notional=200,
            host="127.0.0.1",
            port=8098,
            config_path=root / "galka-live.env",
            data_dir=root / "data",
        )
        self.config.data_dir.mkdir()
        self.gateway = ClassicFakeGateway()
        self.engine = GalkaClassicEngine(self.config, self.gateway)
        self.sleep_engine = patch("live.engine.time.sleep", return_value=None)
        self.sleep_engine.start()

    def tearDown(self):
        self.sleep_engine.stop()
        self.tmp.cleanup()

    def test_unknown_normal_tpsl_child_at_galka_is_accounted_as_target(self):
        self.engine.create_campaign("BTC", 60_000.0, "PLACE_REAL_ORDERS")
        active = self.engine._active_campaign_locked("BTC")

        self.gateway.fill_entry(active, 1, 1_000)
        self.engine._sync_campaign(active)
        managed = float(active["managedNetSize"])
        self.assertGreater(managed, 0)

        # Hyperliquid can execute a normalTpsl trigger under a new child OID.
        # Simulate the venue already flat while the fill OID/CLOID is not in the
        # campaign maps. The close is still economically the GALKA TP.
        for row in list(self.gateway.orders.values()):
            if row.get("coin") == "BTC" and row.get("reduceOnly"):
                self.gateway.orders.pop(int(row["oid"]), None)
        self.gateway.position_sizes["BTC"] = 0.0
        self.gateway.fills.append(
            {
                "coin": "BTC",
                "oid": 9_999_999,
                "cloid": None,
                "price": 60_000.0,
                "size": managed,
                "side": "A",
                "direction": "Close Long",
                "closedPnl": 1.50,
                "fee": 0.09,
                "time": int(active["createdMs"]) + 1_000,
                "hash": "trigger-child-fill",
            }
        )

        self.engine._sync_campaign(active)

        self.assertEqual(active["status"], "completed")
        self.assertAlmostEqual(float(active["cycleClosedPnl"]), 1.50, places=8)
        self.assertAlmostEqual(float(active["managedNetSize"]), 0.0, places=8)
        self.assertGreater(float(active["finalClosedPnl"]), 1.0)
        self.assertNotEqual(active.get("status"), "recovery")

    def test_unknown_close_below_galka_is_not_adopted(self):
        self.engine.create_campaign("BTC", 60_000.0, "PLACE_REAL_ORDERS")
        active = self.engine._active_campaign_locked("BTC")
        self.gateway.fill_entry(active, 1, 1_000)
        self.engine._sync_campaign(active)
        managed = float(active["managedNetSize"])

        prepared = self.engine._prepare_fill_owners(
            active,
            [
                {
                    "coin": "BTC",
                    "oid": 8_888_888,
                    "cloid": None,
                    "price": 59_900.0,
                    "size": managed,
                    "side": "A",
                    "direction": "Close Long",
                    "closedPnl": -0.50,
                    "fee": 0.09,
                    "time": int(active["createdMs"]) + 1_000,
                    "hash": "foreign-loss-close",
                }
            ],
        )

        self.assertIsNone(prepared[0].get("_ownerKind"))


if __name__ == "__main__":
    unittest.main()

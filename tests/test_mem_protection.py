from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from live.hyperliquid_gateway import PlacedOrder
from live.mem_engine import GalkaMemEngine
from test_mem_engine import FakeMemGateway


class ProtectionGateway(FakeMemGateway):
    def place_or_replace_protection(self, coin, quantity, trigger_price, existing_oid=None, cloid=None):
        self._coin(coin)
        if existing_oid:
            self.orders = [row for row in self.orders if int(row["oid"]) != int(existing_oid)]
        self.next_oid += 1
        row = {
            "coin": coin,
            "oid": self.next_oid,
            "cloid": cloid,
            "price": trigger_price,
            "triggerPrice": trigger_price,
            "size": quantity,
            "reduceOnly": True,
            "side": "A",
            "tif": None,
            "isTrigger": True,
        }
        self.orders.append(row)
        return PlacedOrder(self.next_oid, "resting", price=trigger_price, size=quantity, cloid=cloid)

    def fill_protection(self, campaign):
        protection = campaign["protection"]
        order = next(row for row in self.orders if row.get("cloid") == protection["cloid"])
        self.orders = [row for row in self.orders if row.get("cloid") != protection["cloid"]]
        self.fills.append(
            {
                "coin": self.coin,
                "cloid": protection["cloid"],
                "size": order["size"],
                "price": order["triggerPrice"],
            }
        )
        self.position = max(0.0, self.position - order["size"])


class GalkaMemProtectionTests(unittest.TestCase):
    def make_engine(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        config = SimpleNamespace(
            data_dir=Path(temp.name),
            live_enabled=True,
            max_margin_fraction=0.60,
            network_name="mainnet",
            masked_address="0x1234…abcd",
            maker_fee_rate=0.00015,
            taker_fee_rate=0.00045,
        )
        gateway = ProtectionGateway()
        return GalkaMemEngine(config, gateway), gateway

    def create_open_campaign(self, engine, gateway):
        engine.create_campaign(
            "TEST",
            100.0,
            [104.0, 102.0, 100.0],
            100.0,
            3,
            "PLACE_GALKA_MEM_REAL_ORDERS",
        )
        campaign = engine.state["campaign"]
        gateway.fill_entry(campaign, 1)
        engine._sync_campaign()
        return campaign

    def test_break_even_activation_creates_reduce_only_trigger(self):
        engine, gateway = self.make_engine()
        campaign = self.create_open_campaign(engine, gateway)
        engine.reconcile("ACTIVATE_GALKA_MEM_BREAK_EVEN")
        protection = campaign["protection"]
        self.assertTrue(protection["enabled"])
        self.assertGreater(protection["triggerPrice"], 100.0)
        self.assertLess(protection["triggerPrice"], 110.0)
        order = next(row for row in gateway.orders if row.get("cloid") == protection["cloid"])
        self.assertTrue(order["reduceOnly"])
        self.assertTrue(order["isTrigger"])

    def test_protection_can_only_move_up(self):
        engine, gateway = self.make_engine()
        campaign = self.create_open_campaign(engine, gateway)
        engine.reconcile("ACTIVATE_GALKA_MEM_BREAK_EVEN")
        current = float(campaign["protection"]["triggerPrice"])
        engine.reconcile("MOVE_GALKA_MEM_PROTECTION:105")
        self.assertEqual(float(campaign["protection"]["triggerPrice"]), 105.0)
        with self.assertRaisesRegex(Exception, "only move upward"):
            engine.reconcile(f"MOVE_GALKA_MEM_PROTECTION:{max(current, 104.0)}")

    def test_protection_fill_retires_campaign_and_cancels_entries(self):
        engine, gateway = self.make_engine()
        campaign = self.create_open_campaign(engine, gateway)
        engine.reconcile("ACTIVATE_GALKA_MEM_BREAK_EVEN")
        gateway.fill_protection(campaign)
        engine._sync_campaign()
        self.assertEqual(campaign["status"], "completed")
        self.assertIn("protection stop", campaign["completedReason"])
        self.assertEqual(gateway.orders, [])


if __name__ == "__main__":
    unittest.main()

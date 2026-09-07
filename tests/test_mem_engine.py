from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from live.hyperliquid_gateway import PlacedOrder
from live.mem_engine import GalkaMemEngine


class FakeMemGateway:
    def __init__(self):
        self.coin = "TEST"
        self.orders: list[dict] = []
        self.fills: list[dict] = []
        self.position = 0.0
        self.next_oid = 100

    def _coin(self, value):
        if str(value).upper() != self.coin:
            raise ValueError("bad coin")
        return self.coin

    def mids(self):
        return {self.coin: 110.0}

    def max_leverage(self, coin):
        self._coin(coin)
        return 3

    def sz_decimals(self, coin):
        self._coin(coin)
        return 1

    def fresh_account_state(self):
        return {
            "accountValue": 1000.0,
            "withdrawable": 1000.0,
            "totalMarginUsed": abs(self.position) * 100 / 3 if self.position else 0.0,
            "positions": {
                self.coin: {
                    "size": self.position,
                    "entryPrice": 100.0,
                    "liquidationPrice": 80.0,
                }
            } if self.position else {},
        }

    def account_state(self):
        return self.fresh_account_state()

    def fresh_open_orders(self, coin=None):
        if coin is not None:
            self._coin(coin)
        return [dict(row) for row in self.orders]

    def fills_since(self, _start_ms):
        return [dict(row) for row in self.fills]

    def set_leverage_value(self, coin, leverage):
        self._coin(coin)
        if leverage != 3:
            raise ValueError("bad leverage")
        return {"status": "ok"}

    def place_limit_order(self, *, coin, is_buy, size, price, reduce_only, tif, cloid):
        self._coin(coin)
        self.next_oid += 1
        row = {
            "coin": coin,
            "oid": self.next_oid,
            "cloid": cloid,
            "price": price,
            "size": size,
            "reduceOnly": reduce_only,
            "side": "B" if is_buy else "A",
            "tif": tif,
        }
        self.orders.append(row)
        return PlacedOrder(self.next_oid, "resting", price=price, size=size, cloid=cloid)

    def place_or_replace_target(self, coin, quantity, galka_price, existing_oid=None, cloid=None):
        self._coin(coin)
        if existing_oid:
            self.orders = [row for row in self.orders if int(row["oid"]) != int(existing_oid)]
        self.next_oid += 1
        row = {
            "coin": coin,
            "oid": self.next_oid,
            "cloid": cloid,
            "price": galka_price,
            "size": quantity,
            "reduceOnly": True,
            "side": "A",
            "tif": "Gtc",
        }
        self.orders.append(row)
        return PlacedOrder(self.next_oid, "resting", price=galka_price, size=quantity, cloid=cloid)

    def cancel_oids(self, coin, oids):
        self._coin(coin)
        selected = {int(oid) for oid in oids}
        self.orders = [row for row in self.orders if int(row["oid"]) not in selected]
        return {"status": "ok"}

    def emergency_market_close(self, coin, cloid=None):
        self._coin(coin)
        self.position = 0.0
        self.next_oid += 1
        return PlacedOrder(self.next_oid, "filled", cloid=cloid)

    def market_catalog(self):
        return [{"name": self.coin, "szDecimals": 1, "maxLeverage": 3}]

    def fill_entry(self, campaign, level_index):
        level = next(row for row in campaign["levels"] if row["index"] == level_index)
        self.orders = [row for row in self.orders if row.get("cloid") != level["entryCloid"]]
        self.fills.append(
            {
                "coin": self.coin,
                "cloid": level["entryCloid"],
                "size": level["size"],
                "price": level["price"],
            }
        )
        self.position += level["size"]

    def fill_target(self, campaign, basket):
        target = campaign["targets"][basket]
        target_order = next(row for row in self.orders if row.get("cloid") == target["cloid"])
        self.orders = [row for row in self.orders if row.get("cloid") != target["cloid"]]
        self.fills.append(
            {
                "coin": self.coin,
                "cloid": target["cloid"],
                "size": target_order["size"],
                "price": target_order["price"],
            }
        )
        self.position = max(0.0, self.position - target_order["size"])


class GalkaMemEngineTests(unittest.TestCase):
    def make_engine(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        config = SimpleNamespace(
            data_dir=Path(temp.name),
            live_enabled=True,
            max_margin_fraction=0.60,
            network_name="mainnet",
            masked_address="0x1234…abcd",
        )
        gateway = FakeMemGateway()
        return GalkaMemEngine(config, gateway), gateway

    def create_campaign(self, engine):
        return engine.create_campaign(
            "TEST",
            100.0,
            [104.0, 102.0, 100.0],
            100.0,
            3,
            "PLACE_GALKA_MEM_REAL_ORDERS",
        )

    def test_small_cycle_retires_whole_galka_and_cancels_lower_orders(self):
        engine, gateway = self.make_engine()
        self.create_campaign(engine)
        campaign = engine.state["campaign"]
        gateway.fill_entry(campaign, 1)
        engine._sync_campaign()
        self.assertFalse(campaign["lowerTouched"])
        self.assertTrue(any(row.get("cloid") == campaign["targets"]["upper"]["cloid"] for row in gateway.orders))

        gateway.fill_target(campaign, "upper")
        engine._sync_campaign()
        self.assertEqual(campaign["status"], "completed")
        self.assertIn("small cycle", campaign["completedReason"])
        self.assertEqual(gateway.orders, [])

    def test_lower_fill_switches_to_big_cycle_until_both_baskets_are_flat(self):
        engine, gateway = self.make_engine()
        self.create_campaign(engine)
        campaign = engine.state["campaign"]
        first_lower = next(row for row in campaign["levels"] if row["basket"] == "lower")

        gateway.fill_entry(campaign, 1)
        gateway.fill_entry(campaign, first_lower["index"])
        engine._sync_campaign()
        self.assertTrue(campaign["lowerTouched"])
        self.assertEqual(campaign["cycle"], "big")

        gateway.fill_target(campaign, "lower")
        engine._sync_campaign()
        self.assertNotEqual(campaign["status"], "completed")
        self.assertGreater(gateway.position, 0)

        gateway.fill_target(campaign, "upper")
        engine._sync_campaign()
        self.assertEqual(campaign["status"], "completed")
        self.assertIn("big cycle", campaign["completedReason"])
        self.assertEqual(gateway.orders, [])

    def test_preview_blocks_campaign_when_wallet_margin_guard_is_too_small(self):
        engine, gateway = self.make_engine()
        original = gateway.fresh_account_state

        def small_account():
            state = original()
            state["withdrawable"] = 100.0
            state["accountValue"] = 100.0
            return state

        gateway.fresh_account_state = small_account
        plan = engine.preview("TEST", 100.0, [104.0, 102.0, 100.0], 100.0, 3)
        self.assertFalse(plan["safe"])
        self.assertFalse(plan["marginFractionSafe"])


if __name__ == "__main__":
    unittest.main()

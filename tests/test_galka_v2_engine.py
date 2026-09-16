from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from live.config import LiveConfig
from live.galka_v2_engine import GalkaV2Engine
from live.hyperliquid_gateway import PlacedOrder
from test_live_engine import FakeGateway


class V2FakeGateway(FakeGateway):
    def __init__(self):
        super().__init__()
        self._trace_name = None

    def begin_trace(self, name):
        self._trace_name = name

    def finish_trace(self):
        name, self._trace_name = self._trace_name, None
        return {"name": name, "totalMs": 0.0, "stages": [], "websocket": None}

    def place_entries_batch(self, coin, levels, entry_cloids):
        placed = []
        for level, cloid in zip(levels, entry_cloids):
            oid = self._new_oid()
            self._store_order(
                {
                    "coin": coin,
                    "oid": oid,
                    "cloid": cloid,
                    "side": "B",
                    "price": level.price,
                    "size": level.size,
                    "originalSize": level.size,
                    "reduceOnly": False,
                    "triggerPrice": 0.0,
                    "orderType": "Limit",
                }
            )
            placed.append(
                PlacedOrder(
                    oid=oid,
                    status="resting",
                    level=level.index,
                    price=level.price,
                    size=level.size,
                    cloid=cloid,
                )
            )
        return placed

    def fill_collective_target(self, campaign, time_ms, closed_pnl):
        oid = int(campaign["fallbackTargetOid"])
        order = self.orders.pop(oid)
        size = min(float(order["size"]), self.position_sizes[campaign["coin"]])
        self.position_sizes[campaign["coin"]] = max(
            0.0, self.position_sizes[campaign["coin"]] - size
        )
        self.fills.append(
            {
                "coin": campaign["coin"],
                "oid": oid,
                "cloid": order.get("cloid"),
                "price": order["price"],
                "size": size,
                "side": "A",
                "closedPnl": closed_pnl,
                "fee": order["price"] * size * 0.00015,
                "time": time_ms,
                "hash": f"v2-target-{oid}-{time_ms}",
            }
        )


class GalkaV2EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.config = LiveConfig(
            account_address="0x" + "11" * 20,
            api_secret_key="0x" + "33" * 32,
            mainnet=True,
            live_enabled=True,
            leverage=10,
            isolated=True,
            total_notional=1000,
            host="127.0.0.1",
            port=8098,
            config_path=root / "galka-live.env",
            data_dir=root / "data",
        )
        self.config.data_dir.mkdir()
        self.gateway = V2FakeGateway()
        self.engine = GalkaV2Engine(self.config, self.gateway)
        self.sleep_engine = patch("live.engine.time.sleep", return_value=None)
        self.sleep_compat = patch("live.hyperliquid_compat.time.sleep", return_value=None)
        self.sleep_engine.start()
        self.sleep_compat.start()
        self.setup = {
            "anchorPrice": 60_000.0,
            "anchorTime": 1_000,
            "leftBoundaryPrice": 60_500.0,
            "leftBoundaryTime": 900,
            "rightBoundaryPrice": 60_600.0,
            "rightBoundaryTime": 1_100,
        }

    def tearDown(self):
        self.sleep_compat.stop()
        self.sleep_engine.stop()
        self.tmp.cleanup()

    def active(self):
        with self.engine.lock:
            return self.engine._active_campaign_locked("BTC")

    def create(self):
        return self.engine.create_campaign_v2(
            "BTC", 60_000.0, "PLACE_REAL_ORDERS", self.setup
        )

    def test_create_places_nine_entry_only_orders_with_100_margin_cap(self):
        campaign = self.create()
        self.assertEqual(campaign["strategyVersion"], "galka-v2-fibo-100")
        self.assertEqual(campaign["leverage"], 10)
        self.assertLessEqual(campaign["requiredMargin"], 100.0)
        self.assertEqual(len(campaign["levels"]), 9)
        orders = self.gateway.open_orders("BTC")
        self.assertEqual(len(orders), 9)
        self.assertTrue(all(not row["reduceOnly"] for row in orders))
        self.assertEqual([row["label"] for row in campaign["levels"]], [
            "F0.50", "F0.618", "F0.705", "F0.786", "GALKA", "D1", "D2", "D3", "D4"
        ])

    def test_upper_fill_gets_collective_one_percent_net_target(self):
        self.create()
        campaign = self.active()
        self.gateway.fill_entry(campaign, 1, 1_000)
        self.engine._sync_campaign(campaign)

        self.assertEqual(campaign["v2TargetMode"], "upper_1pct_net")
        self.assertGreater(campaign["v2TargetPrice"], campaign["levels"][0]["averageFillPrice"])
        target = self.gateway.orders[int(campaign["fallbackTargetOid"])]
        self.assertTrue(target["reduceOnly"])
        self.assertAlmostEqual(target["size"], self.gateway.position_sizes["BTC"])

    def test_upper_profit_closes_upper_block_then_lower_orders_keep_waiting(self):
        self.create()
        campaign = self.active()
        self.gateway.fill_entry(campaign, 1, 1_000)
        self.engine._sync_campaign(campaign)
        self.gateway.fill_collective_target(campaign, 2_000, closed_pnl=0.25)
        self.engine._sync_campaign(campaign)

        self.assertEqual(campaign["status"], "waiting")
        self.assertTrue(campaign["v2UpperDone"])
        self.assertFalse(campaign["v2LowerActivated"])
        remaining = self.gateway.open_orders("BTC")
        self.assertEqual(len(remaining), 4)
        remaining_oids = {row["oid"] for row in remaining}
        lower_oids = {
            int(level["oid"])
            for level in campaign["levels"]
            if level["zone"] == "lower"
        }
        self.assertEqual(remaining_oids, lower_oids)

    def test_first_lower_fill_switches_whole_position_target_to_galka(self):
        self.create()
        campaign = self.active()
        self.gateway.fill_entry(campaign, 1, 1_000)
        self.engine._sync_campaign(campaign)
        old_target = int(campaign["fallbackTargetOid"])

        self.gateway.fill_entry(campaign, 6, 1_100)
        self.engine._sync_campaign(campaign)

        self.assertTrue(campaign["v2LowerActivated"])
        self.assertEqual(campaign["v2TargetMode"], "galka")
        self.assertAlmostEqual(campaign["v2TargetPrice"], campaign["galkaPrice"])
        # Hyperliquid may modify the same reduce-only order in place, so an
        # unchanged oid is valid. What matters is the replacement price/size.
        self.assertEqual(int(campaign["fallbackTargetOid"]), old_target)
        target = self.gateway.orders[int(campaign["fallbackTargetOid"])]
        self.assertAlmostEqual(target["price"], campaign["galkaPrice"])
        self.assertAlmostEqual(target["size"], self.gateway.position_sizes["BTC"])
        upper_open = [
            row for row in self.gateway.open_orders("BTC")
            if self.engine._entry_owner(campaign, row) in {1, 2, 3, 4, 5}
        ]
        self.assertEqual(upper_open, [])

    def test_lower_galka_exit_finishes_without_old_l1_rearm(self):
        self.create()
        campaign = self.active()
        self.gateway.fill_entry(campaign, 6, 1_000)
        self.engine._sync_campaign(campaign)
        self.assertEqual(campaign["v2TargetMode"], "galka")

        self.gateway.fill_collective_target(campaign, 2_000, closed_pnl=0.30)
        self.engine._sync_campaign(campaign)

        self.assertEqual(campaign["status"], "completed")
        self.assertIsNone(self.active())
        self.assertEqual(self.gateway.open_orders("BTC"), [])
        self.assertEqual(campaign["l1Cycles"], 0)


if __name__ == "__main__":
    unittest.main()

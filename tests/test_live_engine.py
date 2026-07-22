from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from live.config import LiveConfig
from live.engine import GalkaLiveEngine, LiveEngineError
from live.hyperliquid_gateway import EntryWithTarget, GatewayError, PlacedOrder
from live.live_ladder import build_ladder


class FakeGateway:
    def __init__(self):
        self.next_oid = 1000
        self.orders: dict[int, dict] = {}
        self.order_history: dict[int, dict] = {}
        self.fills: list[dict] = []
        self.position_sizes = {"BTC": 0.0, "ETH": 0.0, "SOL": 0.0}
        self.cancelled: list[int] = []
        self.leverage_updates: list[str] = []
        self.agent_address = "0x" + "22" * 20
        self.reject_cancel = False
        self.reject_leverage = False
        self.reject_emergency = False
        self.reject_target = False

    def sz_decimals(self, coin):
        return {"BTC": 5, "ETH": 4, "SOL": 2}[coin]

    def mids(self):
        return {"BTC": 61_000.0, "ETH": 3_000.0, "SOL": 150.0}

    def account_state(self):
        positions = {}
        total_notional = 0.0
        for coin, size in self.position_sizes.items():
            if abs(size) > 1e-12:
                positions[coin] = {"coin": coin, "size": size}
                total_notional += abs(size) * self.mids()[coin]
        return {
            "accountValue": 250.0,
            "withdrawable": 218.0,
            "totalMarginUsed": total_notional / 10,
            "totalNotionalPosition": total_notional,
            "positions": positions,
            "accountMode": "default",
        }

    def fresh_account_state(self):
        return self.account_state()

    def preview_ladder(self, coin, galka_price, total_notional):
        return build_ladder(galka_price, total_notional, self.sz_decimals(coin))

    def set_leverage(self, coin):
        if self.reject_leverage:
            raise GatewayError("leverage rejected")
        self.leverage_updates.append(coin)
        return {"status": "ok", "response": {"type": "default"}}

    def _new_oid(self):
        self.next_oid += 1
        return self.next_oid

    @staticmethod
    def _copy(row):
        return dict(row)

    def _store_order(self, row):
        self.orders[row["oid"]] = row
        self.order_history[row["oid"]] = dict(row)

    def place_entry_with_target(
        self,
        coin,
        level,
        galka_price,
        entry_cloid=None,
        target_cloid=None,
    ):
        entry_oid = self._new_oid()
        target_oid = self._new_oid()
        self._store_order(
            {
                "coin": coin,
                "oid": entry_oid,
                "cloid": entry_cloid,
                "side": "B",
                "price": level.price,
                "size": level.size,
                "originalSize": level.size,
                "reduceOnly": False,
                "triggerPrice": 0.0,
                "orderType": "Limit",
            }
        )
        self._store_order(
            {
                "coin": coin,
                "oid": target_oid,
                "cloid": target_cloid,
                "side": "A",
                "price": galka_price,
                "size": level.size,
                "originalSize": level.size,
                "reduceOnly": True,
                "triggerPrice": galka_price,
                "orderType": "Take Profit Limit",
            }
        )
        return EntryWithTarget(
            entry=PlacedOrder(
                entry_oid, "resting", level.index, level.price, level.size, entry_cloid
            ),
            target=PlacedOrder(
                target_oid, "resting", level.index, galka_price, level.size, target_cloid
            ),
        )

    def place_or_replace_target(
        self,
        coin,
        quantity,
        galka_price,
        existing_oid=None,
        cloid=None,
    ):
        if self.reject_target:
            raise GatewayError("target rejected")
        oid = existing_oid or self._new_oid()
        self._store_order(
            {
                "coin": coin,
                "oid": oid,
                "cloid": cloid,
                "side": "A",
                "price": galka_price,
                "size": quantity,
                "originalSize": quantity,
                "reduceOnly": True,
                "triggerPrice": 0.0,
                "orderType": "Limit",
            }
        )
        return PlacedOrder(oid, "resting", None, galka_price, quantity, cloid)

    def open_orders(self, coin=None):
        rows = list(self.orders.values())
        return [self._copy(row) for row in rows if coin is None or row["coin"] == coin]

    def fresh_open_orders(self, coin=None):
        return self.open_orders(coin)

    def fills_since(self, _start_ms):
        return [dict(row) for row in self.fills]

    def order_status(self, oid):
        row = self.order_history.get(int(oid))
        return dict(row) if row else None

    def cancel_oids(self, coin, oids):
        if self.reject_cancel:
            raise GatewayError("cancel rejected")
        for oid in oids:
            row = self.orders.get(int(oid))
            if row and row["coin"] == coin:
                self.orders.pop(int(oid), None)
                self.cancelled.append(int(oid))
        return {
            "status": "ok",
            "response": {"type": "cancel", "data": {"statuses": ["success"] * len(oids)}},
        }

    def emergency_market_close(self, coin, cloid=None):
        if self.reject_emergency:
            raise GatewayError("market close rejected")
        self.position_sizes[coin] = 0.0
        oid = self._new_oid()
        row = {
            "coin": coin,
            "oid": oid,
            "cloid": cloid,
            "side": "A",
            "price": self.mids()[coin],
            "size": 0.0,
            "reduceOnly": True,
            "triggerPrice": 0.0,
            "orderType": "Market",
        }
        self.order_history[oid] = row
        return PlacedOrder(oid, "filled", cloid=cloid)

    def candles(self, coin, interval, limit):
        return []

    def fill_entry(self, campaign, index, time_ms, size=None, partial=False):
        level = next(row for row in campaign["levels"] if int(row["index"]) == index)
        oid = int(level["oid"])
        order = self.orders[oid]
        fill_size = float(size if size is not None else order["size"])
        if partial and fill_size < float(order["size"]):
            order["size"] = float(order["size"]) - fill_size
        else:
            self.orders.pop(oid, None)
        self.position_sizes[campaign["coin"]] += fill_size
        self.fills.append(
            {
                "coin": campaign["coin"],
                "oid": oid,
                "cloid": order.get("cloid"),
                "price": level["price"],
                "size": fill_size,
                "side": "B",
                "closedPnl": 0.0,
                "fee": level["price"] * fill_size * 0.00015,
                "time": time_ms,
                "hash": f"entry-{oid}-{time_ms}",
            }
        )

    def fill_target(
        self,
        campaign,
        index,
        time_ms,
        closed_pnl,
        size=None,
        oid_override=None,
        include_cloid=True,
    ):
        level = next(row for row in campaign["levels"] if int(row["index"]) == index)
        oid = int(oid_override if oid_override is not None else level["tpOid"])
        order = self.orders.get(oid) or self.order_history.get(oid)
        fill_size = float(size if size is not None else min(level["size"], self.position_sizes[campaign["coin"]]))
        if oid in self.orders:
            self.orders.pop(oid, None)
        self.position_sizes[campaign["coin"]] = max(
            0.0, self.position_sizes[campaign["coin"]] - fill_size
        )
        self.fills.append(
            {
                "coin": campaign["coin"],
                "oid": oid,
                "cloid": order.get("cloid") if order and include_cloid else None,
                "price": campaign["galkaPrice"],
                "size": fill_size,
                "side": "A",
                "closedPnl": closed_pnl,
                "fee": campaign["galkaPrice"] * fill_size * 0.00015,
                "time": time_ms,
                "hash": f"target-{oid}-{time_ms}",
            }
        )

    def manual_close(self, campaign, time_ms):
        size = self.position_sizes[campaign["coin"]]
        self.position_sizes[campaign["coin"]] = 0.0
        self.fills.append(
            {
                "coin": campaign["coin"],
                "oid": 999_999,
                "cloid": None,
                "price": campaign["galkaPrice"] * 0.98,
                "size": size,
                "side": "A",
                "closedPnl": -5.0,
                "fee": 0.01,
                "time": time_ms,
                "hash": f"manual-{time_ms}",
            }
        )


class LiveEngineTests(unittest.TestCase):
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
            total_notional=200,
            host="127.0.0.1",
            port=8098,
            config_path=root / "galka-live.env",
            data_dir=root / "data",
        )
        self.config.data_dir.mkdir()
        self.gateway = FakeGateway()
        self.engine = GalkaLiveEngine(self.config, self.gateway)
        self.sleep_patch = patch("live.engine.time.sleep", return_value=None)
        self.sleep_patch.start()

    def tearDown(self):
        self.sleep_patch.stop()
        self.tmp.cleanup()

    def active(self, coin="BTC"):
        with self.engine.lock:
            return self.engine._active_campaign_locked(coin)

    def test_l1_rearms_then_l2_finishes_campaign(self):
        campaign = self.engine.create_campaign("BTC", 60_000, "PLACE_REAL_ORDERS")
        active = self.active()
        old_l1_oid = active["levels"][0]["oid"]

        self.gateway.fill_entry(active, 1, 1_000)
        self.engine._sync_campaign(active)
        self.assertEqual(active["cycleDeepest"], 1)

        self.gateway.fill_target(active, 1, 2_000, closed_pnl=0.20)
        self.engine._sync_campaign(active)
        self.assertEqual(active["status"], "waiting")
        self.assertEqual(active["l1Cycles"], 1)
        self.assertNotEqual(active["levels"][0]["oid"], old_l1_oid)

        self.gateway.fill_entry(active, 1, 3_000)
        self.gateway.fill_entry(active, 2, 3_100)
        self.engine._sync_campaign(active)
        self.gateway.fill_target(active, 1, 4_000, closed_pnl=0.20)
        self.gateway.fill_target(active, 2, 4_100, closed_pnl=0.25)
        self.engine._sync_campaign(active)

        self.assertEqual(active["status"], "completed")
        self.assertIsNone(self.active())
        self.assertEqual(self.gateway.open_orders("BTC"), [])

    def test_partial_l1_remainder_is_cancelled_before_rearm(self):
        self.engine.create_campaign("BTC", 60_000, "PLACE_REAL_ORDERS")
        active = self.active()
        old_oid = int(active["levels"][0]["oid"])
        half = float(active["levels"][0]["size"]) / 2
        self.gateway.fill_entry(active, 1, 1_000, size=half, partial=True)
        self.engine._sync_campaign(active)
        self.assertIn(old_oid, self.gateway.orders)

        self.gateway.fill_target(active, 1, 2_000, closed_pnl=0.10, size=half)
        self.engine._sync_campaign(active)

        self.assertNotIn(old_oid, self.gateway.orders)
        l1_entries = [
            row for row in self.gateway.open_orders("BTC")
            if row["side"] == "B" and row.get("cloid") == active["levels"][0]["entryCloid"]
        ]
        self.assertEqual(len(l1_entries), 1)
        self.assertEqual(active["l1Cycles"], 1)

    def test_cancel_waiting_campaign_is_verified(self):
        self.engine.create_campaign("ETH", 2_900, "PLACE_REAL_ORDERS")
        cancelled = self.engine.cancel_waiting_campaign("ETH")
        self.assertEqual(cancelled["status"], "canceled")
        self.assertEqual(self.gateway.open_orders("ETH"), [])

    def test_only_one_live_campaign_can_be_active(self):
        self.engine.create_campaign("BTC", 60_000, "PLACE_REAL_ORDERS")
        with self.assertRaisesRegex(LiveEngineError, "только одну"):
            self.engine.create_campaign("ETH", 2_900, "PLACE_REAL_ORDERS")
        self.assertIsNone(self.active("ETH"))

    def test_cancel_race_enters_recovery_and_keeps_protection(self):
        self.engine.create_campaign("BTC", 60_000, "PLACE_REAL_ORDERS")
        active = self.active()
        self.gateway.position_sizes["BTC"] = float(active["levels"][0]["size"])

        with self.assertRaises(LiveEngineError):
            self.engine.cancel_waiting_campaign("BTC")

        self.assertEqual(active["status"], "recovery")
        self.assertTrue(active["autoRearmBlocked"])
        self.assertTrue(any(row["reduceOnly"] for row in self.gateway.open_orders("BTC")))

    def test_rejected_cancel_never_reports_canceled(self):
        self.engine.create_campaign("BTC", 60_000, "PLACE_REAL_ORDERS")
        active = self.active()
        self.gateway.reject_cancel = True
        with self.assertRaises(LiveEngineError):
            self.engine.cancel_waiting_campaign("BTC")
        self.assertEqual(active["status"], "recovery")
        self.assertNotEqual(active["status"], "canceled")

    def test_rejected_leverage_places_no_orders(self):
        self.gateway.reject_leverage = True
        with self.assertRaises(GatewayError):
            self.engine.create_campaign("BTC", 60_000, "PLACE_REAL_ORDERS")
        self.assertEqual(self.gateway.orders, {})
        self.assertIsNone(self.active())

    def test_failed_emergency_close_never_reports_success(self):
        self.engine.create_campaign("BTC", 60_000, "PLACE_REAL_ORDERS")
        active = self.active()
        self.gateway.fill_entry(active, 1, 1_000)
        self.engine._sync_campaign(active)
        self.gateway.reject_emergency = True

        with self.assertRaises(LiveEngineError):
            self.engine.emergency_close("BTC", "EMERGENCY_CLOSE_REAL_POSITION")

        self.assertNotEqual(active["status"], "emergency_closed")
        self.assertGreater(self.gateway.position_sizes["BTC"], 0)
        self.assertTrue(self.engine.state["system"]["safeMode"])

    def test_manual_stop_never_rearms_l1(self):
        self.engine.create_campaign("BTC", 60_000, "PLACE_REAL_ORDERS")
        active = self.active()
        original_l1_oid = active["levels"][0]["oid"]
        self.gateway.fill_entry(active, 1, 1_000)
        self.engine._sync_campaign(active)
        self.gateway.manual_close(active, 2_000)
        self.engine._sync_campaign(active)

        self.assertEqual(active["status"], "recovery")
        self.assertEqual(active["l1Cycles"], 0)
        self.assertEqual(active["levels"][0]["oid"], original_l1_oid)
        for _ in range(3):
            self.engine._sync_campaign(active)
        self.assertEqual(active["status"], "recovery_closed")
        self.assertEqual(active["l1Cycles"], 0)

    def test_unknown_round_trip_fill_blocks_future_automatic_rearm(self):
        self.engine.create_campaign("BTC", 60_000, "PLACE_REAL_ORDERS")
        active = self.active()
        self.gateway.fills.extend(
            [
                {
                    "coin": "BTC",
                    "oid": 900_001,
                    "cloid": None,
                    "price": 60_100.0,
                    "size": 0.001,
                    "side": "B",
                    "closedPnl": 0.0,
                    "fee": 0.01,
                    "time": active["createdMs"] + 10,
                    "hash": "manual-buy",
                },
                {
                    "coin": "BTC",
                    "oid": 900_002,
                    "cloid": None,
                    "price": 60_110.0,
                    "size": 0.001,
                    "side": "A",
                    "closedPnl": 0.01,
                    "fee": 0.01,
                    "time": active["createdMs"] + 20,
                    "hash": "manual-sell",
                },
            ]
        )
        self.engine._sync_campaign(active)
        self.assertTrue(active["autoRearmBlocked"])

    def test_delayed_target_oid_is_resolved_by_cloid(self):
        self.engine.create_campaign("BTC", 60_000, "PLACE_REAL_ORDERS")
        active = self.active()
        level = active["levels"][0]
        original_target_oid = int(level["tpOid"])
        target_cloid = level["targetCloid"]
        delayed_oid = 77_777
        original_target = self.gateway.orders.pop(original_target_oid)
        self.gateway.order_history[delayed_oid] = {**original_target, "oid": delayed_oid, "cloid": target_cloid}
        active["targetOidMap"].pop(str(original_target_oid), None)
        level["tpOid"] = None

        self.gateway.fill_entry(active, 1, 1_000)
        size = self.gateway.position_sizes["BTC"]
        self.gateway.position_sizes["BTC"] = 0.0
        self.gateway.fills.append(
            {
                "coin": "BTC",
                "oid": delayed_oid,
                "cloid": None,
                "price": active["galkaPrice"],
                "size": size,
                "side": "A",
                "closedPnl": 0.2,
                "fee": 0.01,
                "time": 1_100,
                "hash": "delayed-tp",
            }
        )
        self.engine._sync_campaign(active)

        self.assertEqual(active["l1Cycles"], 1)
        self.assertEqual(active["status"], "waiting")
        self.assertEqual(active["targetOidMap"][str(delayed_oid)], 1)

    def test_corrupt_state_starts_in_safe_mode(self):
        self.config.data_dir.joinpath("state.json").write_text("{bad json", encoding="utf-8")
        engine = GalkaLiveEngine(self.config, self.gateway)
        self.assertTrue(engine.state["system"]["safeMode"])
        self.assertTrue(engine.state["system"]["stateCorrupt"])
        self.assertTrue(list(self.config.data_dir.glob("state.broken-*.json")))

    def test_corrupt_primary_recovers_last_valid_state_in_safe_mode(self):
        with self.engine.lock:
            self.engine.state["system"]["checkpoint"] = "previous"
            self.engine._save_locked()
            self.engine.state["system"]["checkpoint"] = "current"
            self.engine._save_locked()
        self.engine.state_path.write_text("{truncated", encoding="utf-8")
        self.engine.state_path.chmod(0o600)

        recovered = GalkaLiveEngine(self.config, self.gateway)
        self.assertEqual(recovered.state["system"]["checkpoint"], "previous")
        self.assertTrue(recovered.state["system"]["safeMode"])
        self.assertTrue(recovered.state["system"]["stateCorrupt"])
        self.assertIn("state.prev.json", recovered.state["system"]["safeModeReason"])

    def test_complete_crash_temp_is_recovered_but_never_trusted(self):
        state = self.engine._empty_state()
        state["system"]["checkpoint"] = "fsynced-temp"
        temporary = self.config.data_dir / ".state.json.123.crash.tmp"
        temporary.write_text(json.dumps(state), encoding="utf-8")
        temporary.chmod(0o600)

        recovered = GalkaLiveEngine(self.config, self.gateway)
        self.assertEqual(recovered.state["system"]["checkpoint"], "fsynced-temp")
        self.assertTrue(recovered.state["system"]["safeMode"])
        self.assertIn(".state.json.123.crash.tmp", recovered.state["system"]["safeModeReason"])

    def test_state_symlink_is_quarantined_without_touching_target(self):
        target = self.config.data_dir / "unrelated.json"
        target.write_text(json.dumps(self.engine._empty_state()), encoding="utf-8")
        self.engine.state_path.symlink_to(target)
        recovered = GalkaLiveEngine(self.config, self.gateway)
        self.assertTrue(recovered.state["system"]["safeMode"])
        self.assertTrue(target.exists())
        self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["version"], 3)

    def test_startup_orphan_position_sets_safe_mode(self):
        self.gateway.position_sizes["BTC"] = 0.01
        self.engine._initial_reconcile()
        self.assertTrue(self.engine.state["system"]["safeMode"])
        self.assertIn("orphan position", self.engine.state["system"]["safeModeReason"])

    def test_global_watchdog_detects_delayed_orphan_after_campaign_finished(self):
        self.engine.create_campaign("BTC", 60_000, "PLACE_REAL_ORDERS")
        self.engine.cancel_waiting_campaign("BTC")
        self.gateway.position_sizes["BTC"] = 0.002
        risks = self.engine._scan_global_risks()
        self.assertTrue(any("orphan position" in risk for risk in risks))
        self.assertTrue(self.engine.state["system"]["safeMode"])

    def test_reconcile_clears_safe_mode_only_when_exchange_is_clean(self):
        with self.engine.lock:
            self.engine._set_safe_mode_locked("test")
            self.engine._save_locked()
        self.gateway.position_sizes["BTC"] = 0.01
        result = self.engine.reconcile_system("RECONCILE_LOCAL_STATE")
        self.assertTrue(result["safeMode"])
        self.gateway.position_sizes["BTC"] = 0.0
        result = self.engine.reconcile_system("RECONCILE_LOCAL_STATE")
        self.assertFalse(result["safeMode"])

    def test_state_file_is_valid_after_each_atomic_save(self):
        self.engine.create_campaign("BTC", 60_000, "PLACE_REAL_ORDERS")
        data = json.loads(self.engine.state_path.read_text(encoding="utf-8"))
        self.assertEqual(data["version"], 3)
        self.assertIn("BTC", data["campaigns"])
        self.assertTrue(self.engine.state_backup_path.exists())

    def test_non_finite_state_cannot_be_persisted(self):
        with self.engine.lock:
            self.engine.state["system"]["invalid"] = float("nan")
            with self.assertRaises(ValueError):
                self.engine._save_locked()

    def test_dead_started_monitor_blocks_new_real_orders(self):
        self.engine.monitor_thread = SimpleNamespace(ident=123, is_alive=lambda: False)
        with self.assertRaisesRegex(LiveEngineError, "монитор"):
            self.engine.create_campaign("BTC", 60_000, "PLACE_REAL_ORDERS")
        self.assertEqual(self.gateway.orders, {})
        self.assertTrue(self.engine.state["system"]["safeMode"])

    def test_live_off_blocks_cancel_and_emergency_writes(self):
        read_only = GalkaLiveEngine(replace(self.config, live_enabled=False), self.gateway)
        self.gateway.position_sizes["BTC"] = 0.001
        before = (dict(self.gateway.orders), list(self.gateway.cancelled), self.gateway.next_oid)

        with self.assertRaisesRegex(LiveEngineError, "LIVE выключен"):
            read_only.cancel_waiting_campaign("BTC")
        with self.assertRaisesRegex(LiveEngineError, "LIVE выключен"):
            read_only.emergency_close("BTC", "EMERGENCY_CLOSE_REAL_POSITION")

        self.assertEqual(
            (dict(self.gateway.orders), list(self.gateway.cancelled), self.gateway.next_oid),
            before,
        )
        self.assertEqual(self.gateway.position_sizes["BTC"], 0.001)

    def test_live_off_startup_reconcile_is_strictly_read_only(self):
        self.engine.create_campaign("BTC", 60_000, "PLACE_REAL_ORDERS")
        before = (dict(self.gateway.orders), list(self.gateway.cancelled), self.gateway.next_oid)
        read_only = GalkaLiveEngine(replace(self.config, live_enabled=False), self.gateway)

        read_only._initial_reconcile()

        self.assertEqual(
            (dict(self.gateway.orders), list(self.gateway.cancelled), self.gateway.next_oid),
            before,
        )
        self.assertTrue(read_only.state["system"]["safeMode"])
        self.assertIn("LIVE is disabled", read_only.state["system"]["safeModeReason"])

    def test_reconcile_failure_cannot_clear_safe_mode(self):
        self.engine.create_campaign("BTC", 60_000, "PLACE_REAL_ORDERS")
        with self.engine.lock:
            self.engine._set_safe_mode_locked("test")
            self.engine._save_locked()

        with patch.object(self.engine, "_sync_campaign", side_effect=GatewayError("offline")):
            result = self.engine.reconcile_system("RECONCILE_LOCAL_STATE")

        self.assertTrue(result["safeMode"])
        self.assertTrue(any("sync failed" in risk for risk in result["risks"]))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from live.config import LiveConfig
from live.engine import LiveEngineError
from live.galka_v2_fast import FastGalkaV2Engine
from test_galka_v2_engine import V2FakeGateway


class CountingV2Gateway(V2FakeGateway):
    def __init__(self):
        super().__init__()
        self.account_reads = 0
        self.order_reads = 0
        self.cancel_calls = 0

    def fresh_account_state(self):
        self.account_reads += 1
        return super().fresh_account_state()

    def fresh_open_orders(self, coin=None):
        self.order_reads += 1
        return super().fresh_open_orders(coin)

    def cancel_oids(self, coin, oids):
        self.cancel_calls += 1
        return super().cancel_oids(coin, oids)

    def reset_counts(self):
        self.account_reads = 0
        self.order_reads = 0
        self.cancel_calls = 0


class GalkaV2FastTests(unittest.TestCase):
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
        self.gateway = CountingV2Gateway()
        self.engine = FastGalkaV2Engine(self.config, self.gateway)
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

    def create(self):
        return self.engine.create_campaign_v2(
            "BTC", 60_000.0, "PLACE_REAL_ORDERS", self.setup
        )

    def test_pristine_waiting_cancel_uses_one_pre_snapshot_and_one_final_position_read(self):
        self.create()
        self.gateway.reset_counts()

        canceled = self.engine.cancel_waiting_campaign("BTC")

        self.assertEqual(canceled["status"], "canceled")
        self.assertEqual(self.gateway.open_orders("BTC"), [])
        self.assertEqual(self.gateway.cancel_calls, 1)
        self.assertEqual(self.gateway.order_reads, 1)
        self.assertEqual(self.gateway.account_reads, 2)

    def test_fast_cancel_enters_recovery_if_position_appears_before_cancel(self):
        self.create()
        campaign = self.engine._active_campaign_locked("BTC")
        self.gateway.fill_entry(campaign, 1, 1_000)
        self.gateway.reset_counts()

        with self.assertRaises(LiveEngineError):
            self.engine.cancel_waiting_campaign("BTC")

        self.assertEqual(campaign["status"], "recovery")
        self.assertGreater(self.gateway.position_sizes["BTC"], 0)


if __name__ == "__main__":
    unittest.main()

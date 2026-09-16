from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from live.config import LiveConfig
from live.galka_classic_engine import GalkaClassicEngine
from test_live_engine import FakeGateway


class ClassicFakeGateway(FakeGateway):
    def __init__(self):
        super().__init__()
        self._trace_name = None

    def begin_trace(self, name):
        self._trace_name = name

    def finish_trace(self):
        name, self._trace_name = self._trace_name, None
        return {"name": name, "totalMs": 0.0, "stages": [], "websocket": None}

    def place_ladder_batch(
        self,
        coin,
        levels,
        galka_price,
        entry_cloids,
        target_cloids,
    ):
        return [
            self.place_entry_with_target(
                coin,
                level,
                galka_price,
                entry_cloid,
                target_cloid,
            )
            for level, entry_cloid, target_cloid in zip(
                levels, entry_cloids, target_cloids
            )
        ]


class GalkaClassicEngineTests(unittest.TestCase):
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
        self.gateway = ClassicFakeGateway()
        self.engine = GalkaClassicEngine(self.config, self.gateway)
        self.sleep_engine = patch("live.engine.time.sleep", return_value=None)
        self.sleep_engine.start()

    def tearDown(self):
        self.sleep_engine.stop()
        self.tmp.cleanup()

    def active(self):
        with self.engine.lock:
            return self.engine._active_campaign_locked("BTC")

    def test_preview_uses_maximum_available_whole_dollar_margin_with_fee_reserve(self):
        # Fake venue reports $250 equity but only $218 withdrawable. Start from
        # $218, then step down one whole dollar because fees + buffer need cash.
        preview = self.engine.preview("BTC", 60_000.0)
        self.assertEqual(preview["wholeDollarCeiling"], 218.0)
        self.assertEqual(preview["targetMargin"], 217.0)
        self.assertGreater(preview["requiredMargin"], 216.0)
        self.assertLessEqual(preview["requiredMargin"], 217.0)
        self.assertGreaterEqual(
            preview["cashLeftAfterMargin"],
            preview["technicalReserveRequired"],
        )
        self.assertEqual(
            preview["sizingPolicy"],
            "max_available_whole_dollars_with_fee_reserve_v1",
        )
        self.assertEqual(len(preview["levels"]), 8)

    def test_classic_uses_eight_levels_and_l1_exit_finishes_campaign(self):
        campaign = self.engine.create_campaign(
            "BTC", 60_000.0, "PLACE_REAL_ORDERS"
        )
        self.assertEqual(len(campaign["levels"]), 8)
        self.assertEqual(
            [round(float(row["weight"]), 2) for row in campaign["levels"]],
            [0.42, 0.22, 0.12, 0.08, 0.06, 0.04, 0.03, 0.03],
        )
        self.assertEqual(campaign["targetMargin"], 217.0)
        self.assertTrue(campaign["autoSizedFromEquity"])

        active = self.active()
        self.gateway.fill_entry(active, 1, 1_000)
        self.engine._sync_campaign(active)
        self.gateway.fill_target(active, 1, 2_000, closed_pnl=0.20)
        self.engine._sync_campaign(active)

        self.assertEqual(active["status"], "completed")
        self.assertEqual(active["l1Cycles"], 0)
        self.assertIsNone(self.active())
        self.assertEqual(self.gateway.open_orders("BTC"), [])


if __name__ == "__main__":
    unittest.main()

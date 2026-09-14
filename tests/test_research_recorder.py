from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from live.research_recorder import RecorderSettings, ResearchSession


class ResearchRecorderTests(unittest.TestCase):
    def settings(self, root: Path) -> RecorderSettings:
        config = SimpleNamespace(
            data_dir=root,
            research_recorder_enabled=True,
            research_recorder_dir=root / "research" / "galka_campaigns",
            research_l2_depth=20,
            research_windows_ms=(100, 250, 500, 1000, 5000, 10000),
            research_feature_interval_ms=50,
            research_book_bps=(1.0, 2.5, 5.0, 10.0, 25.0),
            research_imbalance_ratio=3.0,
            research_stacked_levels=3,
            research_large_trade_quantile=0.95,
            research_baseline_seconds=10,
            research_footprint_price_step=0.0,
            research_queue_max=5000,
        )
        return RecorderSettings.from_config(config)

    @staticmethod
    def campaign() -> dict:
        return {
            "id": "HL-ETH-TEST-RECORDER",
            "coin": "ETH",
            "status": "waiting",
            "galkaPrice": 100.0,
            "createdAt": "2026-08-14T16:00:00Z",
            "createdMs": 1_786_723_200_000,
            "cycleClosedPnl": 0.0,
            "l1RealizedPnl": 0.0,
            "cycleFees": 0.0,
            "levels": [
                {
                    "index": 1,
                    "depth_pct": 0.15,
                    "price": 99.85,
                    "size": 1.0,
                    "notional": 99.85,
                    "oid": 101,
                    "tpOid": 201,
                    "entryCloid": "entry-1",
                    "targetCloid": "target-1",
                    "filledSize": 0.0,
                    "averageFillPrice": 0.0,
                    "status": "resting",
                }
            ],
        }

    def test_raw_market_data_features_and_metadata_are_separate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            session = ResearchSession(self.settings(root), self.campaign())
            session.start()
            receive_ns = time.time_ns()

            self.assertTrue(
                session.mark_crossed_below(
                    1_786_723_201_000,
                    receive_ns,
                    99.90,
                    "bbo_mid",
                )
            )
            session.enqueue(
                "trade",
                {
                    "schemaVersion": 1,
                    "campaignId": session.campaign_id,
                    "symbol": "ETH",
                    "exchangeTimestampMs": 1_786_723_201_010,
                    "localReceiveTimestampNs": receive_ns + 1_000_000,
                    "localReceiveTimestampMs": (receive_ns + 1_000_000) // 1_000_000,
                    "latencyMs": 1,
                    "aggressorSide": "B",
                    "raw": {
                        "coin": "ETH",
                        "side": "B",
                        "px": "99.80",
                        "sz": "2.0",
                        "time": 1_786_723_201_010,
                        "tid": 12345,
                        "hash": "0xabc",
                    },
                },
            )
            session.enqueue(
                "book",
                {
                    "schemaVersion": 1,
                    "campaignId": session.campaign_id,
                    "symbol": "ETH",
                    "exchangeTimestampMs": 1_786_723_201_020,
                    "localReceiveTimestampNs": receive_ns + 2_000_000,
                    "localReceiveTimestampMs": (receive_ns + 2_000_000) // 1_000_000,
                    "latencyMs": 1,
                    "raw": {
                        "coin": "ETH",
                        "time": 1_786_723_201_020,
                        "levels": [
                            [{"px": "99.70", "sz": "3.0", "n": 2}],
                            [{"px": "99.90", "sz": "2.0", "n": 1}],
                        ],
                    },
                },
            )
            self.assertTrue(
                session.mark_return_to_galka(
                    1_786_723_202_000,
                    receive_ns + 3_000_000,
                    100.0,
                    "bbo_mid",
                )
            )
            session.observe_price(1_786_723_203_000, 101.0)
            session.observe_price(1_786_723_205_000, 99.5)
            session.request_finalize()
            session._thread.join(timeout=3.0)
            self.assertFalse(session._thread.is_alive())

            campaign_dir = root / "research" / "galka_campaigns" / "ETH" / session.campaign_id
            self.assertTrue((campaign_dir / "metadata.json").is_file())
            self.assertTrue((campaign_dir / "trades.raw.jsonl").is_file())
            self.assertTrue((campaign_dir / "orderbook.raw.jsonl").is_file())
            self.assertTrue((campaign_dir / "features.jsonl").is_file())
            self.assertTrue((campaign_dir / "footprint.json").is_file())
            self.assertTrue((campaign_dir / "dataset_manifest.json").is_file())

            metadata = json.loads((campaign_dir / "metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["crossedBelowGalka"]["price"], 99.90)
            self.assertEqual(metadata["returnToGalka"]["price"], 100.0)
            self.assertGreaterEqual(metadata["maxDeviationBelowGalkaPct"], 0.1)

            trade = json.loads((campaign_dir / "trades.raw.jsonl").read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(trade["raw"]["tid"], 12345)
            self.assertIn("localReceiveTimestampNs", trade)

            feature = json.loads((campaign_dir / "features.jsonl").read_text(encoding="utf-8").splitlines()[0])
            self.assertIn("100", feature["windows"])
            self.assertIn("cvd", feature)
            self.assertIn("book", feature)

            manifest = json.loads((campaign_dir / "dataset_manifest.json").read_text(encoding="utf-8"))
            raw_files = {row["name"]: row["gitSync"] for row in manifest["files"]}
            self.assertFalse(raw_files["trades.raw.jsonl"])
            self.assertFalse(raw_files["orderbook.raw.jsonl"])

    def test_recorder_is_armed_before_cross_but_not_recording(self):
        with tempfile.TemporaryDirectory() as temporary:
            session = ResearchSession(self.settings(Path(temporary)), self.campaign())
            self.assertFalse(session.recording)
            self.assertEqual(session.crossed_below_ms, 0)
            self.assertEqual(session.metadata["status"], "armed")


if __name__ == "__main__":
    unittest.main()

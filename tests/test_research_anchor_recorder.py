from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from live.research_anchor_recorder import AnchoredResearchSession
from live.research_recorder import RecorderSettings


class AnchoredResearchRecorderTests(unittest.TestCase):
    @staticmethod
    def settings(root: Path) -> RecorderSettings:
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
        created_ms = 1_787_330_400_000
        return {
            "id": "HL-ETH-ANCHOR-TEST",
            "coin": "ETH",
            "status": "waiting",
            "galkaPrice": 2369.51,
            "createdAt": "2026-08-21T17:20:00Z",
            "createdMs": created_ms,
            "researchSetup": {
                "schemaVersion": 1,
                "selectionMethod": "manual_crosshair_structure_v1",
                "symbol": "ETH",
                "timeframe": "5m",
                "galkaLevel": 2369.51,
                "anchorTimeMs": created_ms - 3_600_000,
                "structureEndTimeMs": created_ms - 600_000,
                "lockedForCampaign": True,
                "researchOnly": True,
                "structureBars": [],
            },
            "levels": [],
        }

    def test_annotated_campaign_records_from_placement_and_preserves_cross_event(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            campaign = self.campaign()
            session = AnchoredResearchSession(self.settings(root), campaign)

            self.assertTrue(session.recording)
            self.assertEqual(session.metadata["recordingStartedAt"], campaign["createdAt"])
            self.assertEqual(session.metadata["recordingStartBasis"], "manual_galka_structure_locked")
            self.assertEqual(session.metadata["galkaStructure"]["anchorTimeMs"], campaign["researchSetup"]["anchorTimeMs"])
            self.assertEqual(session.metadata["galkaStructure"]["galkaLevel"], 2369.51)

            session.start()
            receive_ns = time.time_ns()
            self.assertTrue(
                session.mark_crossed_below(
                    campaign["createdMs"] + 15_000,
                    receive_ns,
                    2368.90,
                    "bbo_mid",
                )
            )
            self.assertEqual(session.metadata["recordingStartedAt"], campaign["createdAt"])
            self.assertTrue(session.metadata["crossedBelowGalka"]["captureAlreadyActive"])
            session.request_finalize()
            session._thread.join(timeout=3.0)
            self.assertFalse(session._thread.is_alive())

            metadata_path = (
                root
                / "research"
                / "galka_campaigns"
                / "ETH"
                / campaign["id"]
                / "metadata.json"
            )
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            self.assertEqual(metadata["recordingStartBasis"], "manual_galka_structure_locked")
            self.assertEqual(metadata["galkaStructure"]["structureEndTimeMs"], campaign["researchSetup"]["structureEndTimeMs"])

    def test_unannotated_campaign_keeps_legacy_armed_behavior(self):
        with tempfile.TemporaryDirectory() as temporary:
            campaign = self.campaign()
            campaign.pop("researchSetup")
            session = AnchoredResearchSession(self.settings(Path(temporary)), campaign)
            self.assertFalse(session.recording)
            self.assertIsNone(session.metadata["recordingStartedAt"])
            self.assertEqual(session.metadata["status"], "armed")


if __name__ == "__main__":
    unittest.main()

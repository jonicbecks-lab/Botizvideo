from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from live.hyperliquid_safe_compat import SafeCompatibleGalkaLiveEngine
from live.live_ladder import round_perp_price
from live.research_journal import ResearchJournal


class _Gateway:
    def sz_decimals(self, _coin: str) -> int:
        return 4


class SafeTargetValidationTests(unittest.TestCase):
    def test_rounded_fallback_limit_is_valid_galka_target(self) -> None:
        engine = SafeCompatibleGalkaLiveEngine.__new__(SafeCompatibleGalkaLiveEngine)
        engine.gateway = _Gateway()
        campaign = {"coin": "ETH", "galkaPrice": 1912.66}
        expected = round_perp_price(campaign["galkaPrice"], engine.gateway.sz_decimals("ETH"))
        order = {
            "reduceOnly": True,
            "side": "A",
            "triggerPrice": 0.0,
            "price": expected,
        }
        self.assertTrue(engine._is_galka_target(campaign, order))

    def test_wrong_target_price_is_rejected(self) -> None:
        engine = SafeCompatibleGalkaLiveEngine.__new__(SafeCompatibleGalkaLiveEngine)
        engine.gateway = _Gateway()
        campaign = {"coin": "ETH", "galkaPrice": 1912.66}
        order = {
            "reduceOnly": True,
            "side": "A",
            "triggerPrice": 0.0,
            "price": 1900.0,
        }
        self.assertFalse(engine._is_galka_target(campaign, order))


class ResearchJournalTests(unittest.TestCase):
    def test_campaign_snapshot_contains_research_fields_without_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            journal = ResearchJournal(root / "data", root / "repo")
            campaign = {
                "id": "HL-ETH-test",
                "coin": "ETH",
                "status": "closed",
                "createdAt": "2026-08-10T00:00:00Z",
                "completedAt": "2026-08-10T00:10:00Z",
                "galkaPrice": 1900.0,
                "setupMidPrice": 1920.0,
                "leverage": 10,
                "isolated": True,
                "requestedNotional": 2000.0,
                "actualNotional": 1999.0,
                "cycleDeepest": 2,
                "l1Cycles": 1,
                "l1RealizedPnl": 1.0,
                "cycleClosedPnl": 2.0,
                "cycleFees": 0.2,
                "levels": [
                    {
                        "index": 1,
                        "depth_pct": 0.1,
                        "price": 1898.0,
                        "size": 0.4,
                        "notional": 759.2,
                        "filledSize": 0.4,
                        "averageFillPrice": 1898.0,
                        "status": "filled",
                    }
                ],
            }
            journal.upsert_campaign(campaign, reason="test")
            path = root / "data" / "research" / "campaigns" / "HL-ETH-test.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["cycleDeepest"], 2)
            self.assertEqual(payload["l1Cycles"], 1)
            self.assertIn("postExitResearch", payload)
            serialized = path.read_text(encoding="utf-8").lower()
            self.assertNotIn("privatekey", serialized)
            self.assertNotIn("session-token", serialized)


if __name__ == "__main__":
    unittest.main()

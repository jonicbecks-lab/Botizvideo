from __future__ import annotations

import unittest

from live.research_engine import ResearchCompatibleGalkaLiveEngine


class ResearchStructureV2Tests(unittest.TestCase):
    def engine(self) -> ResearchCompatibleGalkaLiveEngine:
        return object.__new__(ResearchCompatibleGalkaLiveEngine)

    def test_freeform_boundaries_keep_exact_time_and_price(self):
        setup = {
            "selectionMethod": "manual_crosshair_structure_v2",
            "timeframe": "5m",
            "anchorTimeMs": 1_800_000,
            "anchorPrice": 100.0,
            "leftBoundaryTimeMs": 1_612_345,
            "leftBoundaryPrice": 101.37,
            "rightBoundaryTimeMs": 2_034_567,
            "rightBoundaryPrice": 103.42,
            "structureStartTimeMs": 1_612_345,
            "structureEndTimeMs": 2_034_567,
            "structureBars": [
                {"timeMs": 1_500_000, "open": 102, "high": 103, "low": 101, "close": 102.5, "volume": 4},
                {"timeMs": 1_800_000, "open": 101, "high": 102, "low": 99, "close": 100.5, "volume": 5},
                {"timeMs": 2_100_000, "open": 100, "high": 104, "low": 100, "close": 103, "volume": 6},
            ],
            "lockedForCampaign": True,
        }
        normalized = self.engine()._normalize_research_setup("ETH", 100.0, setup)
        self.assertIsNotNone(normalized)
        assert normalized is not None
        self.assertEqual(normalized["selectionMethod"], "manual_crosshair_structure_v2")
        self.assertEqual(normalized["anchorTimeMs"], 1_800_000)
        self.assertEqual(normalized["leftBoundaryTimeMs"], 1_612_345)
        self.assertAlmostEqual(normalized["leftBoundaryPrice"], 101.37)
        self.assertEqual(normalized["rightBoundaryTimeMs"], 2_034_567)
        self.assertAlmostEqual(normalized["rightBoundaryPrice"], 103.42)
        self.assertEqual(normalized["structureStartTimeMs"], 1_612_345)
        self.assertEqual(normalized["structureEndTimeMs"], 2_034_567)
        self.assertTrue(normalized["researchOnly"])
        self.assertTrue(normalized["lockedForCampaign"])
        self.assertAlmostEqual(normalized["derived"]["leftBoundaryVsGalkaPct"], 1.37)
        self.assertAlmostEqual(normalized["derived"]["rightBoundaryVsGalkaPct"], 3.42)

    def test_legacy_v1_is_migrated_without_affecting_execution_price(self):
        setup = {
            "selectionMethod": "manual_crosshair_structure_v1",
            "timeframe": "5m",
            "anchorTimeMs": 1_500_000,
            "structureEndTimeMs": 2_100_000,
            "lockedForCampaign": True,
        }
        normalized = self.engine()._normalize_research_setup("BTC", 70000.0, setup)
        self.assertIsNotNone(normalized)
        assert normalized is not None
        self.assertEqual(normalized["selectionMethod"], "manual_crosshair_structure_v2")
        self.assertEqual(normalized["leftBoundaryTimeMs"], 1_500_000)
        self.assertEqual(normalized["rightBoundaryTimeMs"], 2_100_000)
        self.assertEqual(normalized["leftBoundaryPrice"], 70000.0)
        self.assertEqual(normalized["rightBoundaryPrice"], 70000.0)
        self.assertEqual(normalized["galkaLevel"], 70000.0)


if __name__ == "__main__":
    unittest.main()

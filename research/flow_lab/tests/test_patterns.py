import unittest

from research.flow_lab.patterns import (
    LiquidationRegimeDetector,
    PatternThresholds,
    classify_cross_venue,
    classify_spot_perp,
    response_pattern,
)


class PatternCatalogTests(unittest.TestCase):
    def test_response_patterns_keep_distinct_families(self):
        base = {"delta_usd": 100.0, "response_class": "continuation_candidate"}
        self.assertEqual(response_pattern(base)["pattern_family"], "continuation")
        base["response_class"] = "absorption_candidate"
        self.assertEqual(response_pattern(base)["pattern_family"], "absorption")
        self.assertEqual(response_pattern(base)["hypothesis_direction"], -1)
        base["response_class"] = "instant_reversal"
        self.assertEqual(response_pattern(base)["pattern_family"], "instant_reversal")

    def test_cross_venue_consensus(self):
        row = {
            "asset": "BTC", "market": "perp", "start_ms": 1, "end_ms": 2,
            "equal_venue_zscore": 1.4, "venue_zscore_dispersion": 0.2,
            "sign_consensus": 1.0,
            "venue_details": [
                {"exchange": "binance", "zscore_past_only": 1.2},
                {"exchange": "bybit", "zscore_past_only": 1.5},
                {"exchange": "okx", "zscore_past_only": 1.6},
            ],
        }
        self.assertEqual(classify_cross_venue(row)["pattern_label"], "cross_venue_consensus_buy")

    def test_cross_venue_divergence_beats_average(self):
        row = {
            "asset": "ETH", "market": "perp", "start_ms": 1, "end_ms": 2,
            "equal_venue_zscore": 0.1, "venue_zscore_dispersion": 1.6,
            "sign_consensus": 1 / 3,
            "venue_details": [
                {"exchange": "binance", "zscore_past_only": 1.7},
                {"exchange": "bybit", "zscore_past_only": -1.5},
                {"exchange": "okx", "zscore_past_only": 0.1},
            ],
        }
        self.assertEqual(classify_cross_venue(row)["pattern_label"], "cross_venue_divergence")

    def test_spot_perp_opposition_and_lead(self):
        spot = {"asset": "BTC", "start_ms": 10, "equal_venue_zscore": -1.0}
        perp = {"asset": "BTC", "start_ms": 10, "equal_venue_zscore": 1.2}
        self.assertEqual(
            classify_spot_perp(spot, perp)["pattern_label"],
            "perp_buy_spot_sell_divergence",
        )
        spot["equal_venue_zscore"] = 0.05
        self.assertEqual(classify_spot_perp(spot, perp)["pattern_label"], "perp_leads_buy")

    def test_liquidation_detector_is_past_only(self):
        cfg = PatternThresholds(liquidation_extreme_percentile=0.75)
        detector = LiquidationRegimeDetector(cfg, min_history=4, history_maxlen=100)
        for i, gross in enumerate((10.0, 20.0, 30.0, 40.0)):
            self.assertIsNone(detector.observe(
                asset="BTC", market="perp", start_ms=i,
                long_liq_usd=gross, short_liq_usd=0.0,
                price_return_bps=-2.0, delta_flow_usd=-100.0,
            ))
        event = detector.observe(
            asset="BTC", market="perp", start_ms=5,
            long_liq_usd=100.0, short_liq_usd=10.0,
            price_return_bps=-5.0, delta_flow_usd=-500.0,
        )
        self.assertIsNotNone(event)
        self.assertEqual(event["pattern_label"], "long_liquidation_driven_candidate")
        self.assertTrue(event["causal_trigger"])


if __name__ == "__main__":
    unittest.main()

import unittest

from research.flow_lab.exhaustion import CausalExhaustionDetector


class ExhaustionTests(unittest.TestCase):
    def test_triggers_only_after_decay_is_observed(self):
        d = CausalExhaustionDetector()
        self.assertIsNone(d.observe("BTC", "perp", 10, 1_000_000, 0.995, 2.0))
        self.assertIsNone(d.observe("BTC", "perp", 11, 800_000, 0.90, 1.2))
        row = d.observe("BTC", "perp", 12, 400_000, 0.80, 0.2)
        self.assertIsNotNone(row)
        self.assertEqual(row["trigger_bucket_index"], 12)
        self.assertEqual(row["peak_bucket_index"], 10)
        self.assertTrue(row["causal_trigger"])
        self.assertAlmostEqual(row["flow_ratio_to_peak"], 0.4)

    def test_no_trigger_while_price_still_extends(self):
        d = CausalExhaustionDetector()
        d.observe("ETH", "perp", 5, -500_000, 0.999, 2.0)
        self.assertIsNone(d.observe("ETH", "perp", 6, -200_000, 0.7, 0.8))

    def test_stronger_same_side_flow_moves_causal_peak_forward(self):
        d = CausalExhaustionDetector()
        d.observe("BTC", "perp", 1, 100, 0.995, 2.0)
        d.observe("BTC", "perp", 2, 150, 0.999, 2.2)
        row = d.observe("BTC", "perp", 3, 50, 0.5, 0.1)
        self.assertEqual(row["peak_bucket_index"], 2)
        self.assertEqual(row["peak_abs_flow_usd"], 150.0)

    def test_opposite_flow_is_not_same_side_exhaustion(self):
        d = CausalExhaustionDetector()
        d.observe("BTC", "perp", 1, 100, 0.995, 2.0)
        self.assertIsNone(d.observe("BTC", "perp", 2, -20, 0.5, -0.2))
        self.assertIsNone(d.observe("BTC", "perp", 3, 20, 0.5, 0.1))

    def test_episode_expires(self):
        d = CausalExhaustionDetector(max_followup_buckets=2)
        d.observe("BTC", "perp", 1, 100, 0.995, 2.0)
        self.assertIsNone(d.observe("BTC", "perp", 4, 20, 0.5, 0.1))


if __name__ == "__main__":
    unittest.main()

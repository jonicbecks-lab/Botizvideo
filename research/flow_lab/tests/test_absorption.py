import unittest

from research.flow_lab.absorption import orderbook_absorption_evidence


class AbsorptionEvidenceTests(unittest.TestCase):
    def test_sell_flow_uses_bid_refill(self):
        row = orderbook_absorption_evidence(
            -1000.0, -0.5, 2.0,
            {"depth_change_valid": True, "bid_add_usd": 800.0, "ask_add_usd": 50.0},
        )
        self.assertTrue(row["absorption_evidence_valid"])
        self.assertEqual(row["absorption_direction"], "bid_absorption")
        self.assertAlmostEqual(row["signed_impact_units_past_only"], 0.25)
        self.assertAlmostEqual(row["relevant_refill_vs_flow"], 0.8)
        self.assertAlmostEqual(row["absorption_evidence_score"], 0.775)

    def test_buy_flow_uses_ask_refill(self):
        row = orderbook_absorption_evidence(
            2000.0, 0.0, 2.0,
            {"depth_change_valid": True, "bid_add_usd": 0.0, "ask_add_usd": 1000.0},
        )
        self.assertEqual(row["absorption_direction"], "ask_absorption")
        self.assertAlmostEqual(row["absorption_evidence_score"], 0.75)

    def test_invalid_without_aligned_book_or_scale(self):
        self.assertFalse(orderbook_absorption_evidence(
            -1000.0, 0.0, None, {"depth_change_valid": True, "bid_add_usd": 1000.0}
        )["absorption_evidence_valid"])
        self.assertFalse(orderbook_absorption_evidence(
            -1000.0, 0.0, 2.0, {"depth_change_valid": False}
        )["absorption_evidence_valid"])


if __name__ == "__main__":
    unittest.main()

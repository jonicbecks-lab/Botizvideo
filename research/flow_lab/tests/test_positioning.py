import unittest

from research.flow_lab.positioning import classify_oi_positioning


class PositioningTests(unittest.TestCase):
    def test_new_longs(self):
        row = classify_oi_positioning(4.0, 1_000_000.0, 500_000.0)
        self.assertEqual(row["positioning_label"], "new_longs_building")
        self.assertEqual(row["price_flow_alignment"], 1)

    def test_new_shorts(self):
        row = classify_oi_positioning(-4.0, 1_000_000.0, -500_000.0)
        self.assertEqual(row["positioning_label"], "new_shorts_building")

    def test_short_unwind(self):
        row = classify_oi_positioning(4.0, -1_000_000.0, 500_000.0)
        self.assertEqual(row["positioning_label"], "short_unwind_or_liquidation")

    def test_build_without_price_extension(self):
        row = classify_oi_positioning(0.1, 1_000_000.0, -500_000.0)
        self.assertEqual(row["positioning_label"], "position_build_without_price_extension")


if __name__ == "__main__":
    unittest.main()

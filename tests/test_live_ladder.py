import unittest

from live.live_ladder import (
    MANUAL_DEPTHS,
    MANUAL_WEIGHTS,
    MIN_ORDER_NOTIONAL,
    build_ladder,
    estimated_target_pnl_mixed,
    weighted_average,
)


class LiveLadderTests(unittest.TestCase):
    def test_live_ladder_places_all_eight_classic_orders(self):
        levels = build_ladder(60_000, 150, 5)
        self.assertEqual(len(levels), 8)
        self.assertEqual(tuple(level.index for level in levels), tuple(range(1, 9)))
        self.assertEqual(tuple(level.depth_pct for level in levels), MANUAL_DEPTHS)
        self.assertEqual(tuple(level.weight for level in levels), MANUAL_WEIGHTS)
        self.assertEqual(MANUAL_WEIGHTS, (0.42, 0.22, 0.12, 0.08, 0.06, 0.04, 0.03, 0.03))
        self.assertTrue(all(level.notional >= float(MIN_ORDER_NOTIONAL) for level in levels))
        self.assertLessEqual(sum(level.notional for level in levels), 150.01)
        self.assertLess(weighted_average(levels), 60_000)

    def test_too_small_total_is_rejected_for_eight_orders(self):
        with self.assertRaisesRegex(ValueError, "require at least"):
            build_ladder(60_000, 30, 5)

    def test_sol_precision_and_l8_depth(self):
        levels = build_ladder(150, 150, 2)
        self.assertAlmostEqual(levels[-1].price, 147.0, places=4)
        self.assertEqual(levels[-1].index, 8)
        self.assertTrue(all(level.notional >= 10 for level in levels))

    def test_mixed_fee_preview_uses_separate_entry_and_exit_rates(self):
        levels = build_ladder(60_000, 200, 5)
        maker_maker = estimated_target_pnl_mixed(levels, 60_000, 0.00015, 0.00015)
        maker_taker = estimated_target_pnl_mixed(levels, 60_000, 0.00015, 0.00045)
        self.assertLess(maker_taker, maker_maker)
        expected_difference = 60_000 * sum(level.size for level in levels) * 0.00030
        self.assertAlmostEqual(maker_maker - maker_taker, expected_difference, places=8)


if __name__ == "__main__":
    unittest.main()

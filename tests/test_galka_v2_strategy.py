import unittest

from live.galka_v2_strategy import (
    FULL_PLAN_MIN_NET_AT_GALKA_USD,
    V2_LEVERAGE,
    V2_MARGIN_USD,
    V2_TOTAL_NOTIONAL,
    build_v2_plan,
    fibonacci_entry_prices,
    upper_take_profit_price,
)


class GalkaV2StrategyTests(unittest.TestCase):
    def test_fibonacci_is_measured_from_right_leg_high_back_to_galka(self):
        prices = fibonacci_entry_prices(100.0, 110.0)
        self.assertAlmostEqual(prices[0], 105.0, places=8)
        self.assertAlmostEqual(prices[1], 103.82, places=8)
        self.assertAlmostEqual(prices[2], 102.95, places=8)
        self.assertAlmostEqual(prices[3], 102.14, places=8)
        self.assertAlmostEqual(prices[4], 100.0, places=8)

    def test_test_campaign_is_fixed_to_100_margin_at_10x(self):
        self.assertEqual(V2_MARGIN_USD, 100.0)
        self.assertEqual(V2_LEVERAGE, 10)
        self.assertEqual(V2_TOTAL_NOTIONAL, 1000.0)

    def test_normal_right_leg_uses_40_60_split_and_nine_entries(self):
        plan = build_v2_plan(60_000.0, 60_600.0, 5)
        self.assertEqual(len(plan.levels), 9)
        self.assertAlmostEqual(plan.upper_share, 0.40)
        self.assertAlmostEqual(plan.lower_share, 0.60)
        self.assertEqual([row.zone for row in plan.levels[:5]], ["upper"] * 5)
        self.assertEqual([row.zone for row in plan.levels[5:]], ["lower"] * 4)
        self.assertEqual([row.label for row in plan.levels], [
            "F0.50", "F0.618", "F0.705", "F0.786", "GALKA", "D1", "D2", "D3", "D4"
        ])
        self.assertGreaterEqual(plan.full_fill_net_at_galka, FULL_PLAN_MIN_NET_AT_GALKA_USD)
        self.assertLess(plan.weighted_average, plan.galka_price)

    def test_tall_right_leg_reduces_upper_share_automatically(self):
        plan = build_v2_plan(60_000.0, 61_800.0, 5)
        self.assertLess(plan.upper_share, 0.40)
        self.assertGreaterEqual(plan.upper_share, 0.10)
        self.assertGreaterEqual(plan.full_fill_net_at_galka, FULL_PLAN_MIN_NET_AT_GALKA_USD)

    def test_upper_tp_targets_one_percent_of_used_margin_net(self):
        # $400 entry notional at 10x uses $40 margin, so target net is $0.40.
        quantity = 4.0
        entry_notional = 400.0
        entry_fees = 0.06
        exit_fee_rate = 0.00015
        price = upper_take_profit_price(
            quantity=quantity,
            entry_notional=entry_notional,
            entry_fees_paid=entry_fees,
            exit_fee_rate=exit_fee_rate,
        )
        exit_notional = price * quantity
        net = exit_notional - entry_notional - entry_fees - exit_notional * exit_fee_rate
        self.assertAlmostEqual(net, 0.40, places=8)


if __name__ == "__main__":
    unittest.main()

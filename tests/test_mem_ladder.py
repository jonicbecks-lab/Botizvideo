from __future__ import annotations

import unittest

from live.mem_ladder import build_mem_plan


class GalkaMemPlannerTests(unittest.TestCase):
    def test_cashcat_style_3x_plan_passes_liquidation_buffer(self):
        galka = 0.1866
        plan = build_mem_plan(
            galka_price=galka,
            upper_prices=[galka * 1.04, galka * 1.03, galka * 1.02, galka],
            campaign_margin=100,
            leverage=3,
            max_leverage=3,
            sz_decimals=5,
        )
        self.assertTrue(plan["safe"])
        self.assertEqual(len(plan["levels"]), 8)
        lower = [row for row in plan["levels"] if row["basket"] == "lower"]
        self.assertEqual(len(lower), 4)
        self.assertAlmostEqual(lower[0]["offset_pct"], -2.0, delta=0.02)
        self.assertAlmostEqual(lower[-1]["offset_pct"], -8.0, delta=0.02)
        self.assertAlmostEqual(plan["upperTargetMovePct"], 3.5, places=6)
        self.assertAlmostEqual(plan["requiredMaxLiquidationPrice"], lower[-1]["price"] * 0.95, places=10)
        self.assertTrue(all(row["safe"] for row in plan["liquidationStates"]))

    def test_two_upper_levels_use_entire_upper_budget_with_increasing_weight(self):
        galka = 100.0
        plan = build_mem_plan(
            galka_price=galka,
            upper_prices=[104.0, 101.0],
            campaign_margin=100,
            leverage=3,
            max_leverage=3,
            sz_decimals=4,
        )
        upper = [row for row in plan["levels"] if row["basket"] == "upper"]
        self.assertEqual([row["weight"] for row in upper], [1.0, 1.5])
        self.assertGreater(upper[1]["margin"], upper[0]["margin"])
        self.assertAlmostEqual(sum(row["margin"] for row in upper), 100 / 3, delta=0.03)

    def test_high_effective_leverage_fails_prefix_liquidation_gate(self):
        galka = 100.0
        plan = build_mem_plan(
            galka_price=galka,
            upper_prices=[104.0, 102.0, 100.0],
            campaign_margin=100,
            leverage=10,
            max_leverage=10,
            sz_decimals=4,
        )
        self.assertFalse(plan["safe"])
        self.assertTrue(any(not row["safe"] for row in plan["liquidationStates"]))

    def test_upper_level_above_five_percent_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "between GALKA and \+5%"):
            build_mem_plan(
                galka_price=100.0,
                upper_prices=[105.01],
                campaign_margin=100,
                leverage=3,
                max_leverage=3,
                sz_decimals=4,
            )

    def test_campaign_margin_is_capped_at_one_hundred(self):
        with self.assertRaisesRegex(ValueError, "between \$0 and \$100"):
            build_mem_plan(
                galka_price=100.0,
                upper_prices=[102.0],
                campaign_margin=101,
                leverage=3,
                max_leverage=3,
                sz_decimals=4,
            )


if __name__ == "__main__":
    unittest.main()

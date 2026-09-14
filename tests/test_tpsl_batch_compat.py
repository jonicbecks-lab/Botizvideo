from __future__ import annotations

import unittest

from live.hyperliquid_compat import CompatibleGalkaLiveEngine
from live.live_ladder import LadderLevel, build_ladder
from live.tpsl_batch_compat import _place_ladder_batch_pairwise, install


class _Gateway:
    def __init__(self):
        self.calls = []
        self.write_checks = []

    def _require_live_write(self, operation):
        self.write_checks.append(operation)

    def _coin(self, coin):
        return str(coin).upper()

    def place_entry_with_target(
        self,
        coin,
        level,
        galka_price,
        entry_cloid=None,
        target_cloid=None,
    ):
        row = (coin, level.index, galka_price, entry_cloid, target_cloid)
        self.calls.append(row)
        return row


class TpslBatchCompatTests(unittest.TestCase):
    def test_bnb_ladder_uses_pairwise_parent_target_submission(self):
        gateway = _Gateway()
        levels = [
            LadderLevel(
                index=index,
                depth_pct=float(index),
                weight=0.125,
                price=700.0 - index,
                size=0.1,
                notional=(700.0 - index) * 0.1,
            )
            for index in range(1, 9)
        ]
        entry_cloids = [f"entry-{index}" for index in range(1, 9)]
        target_cloids = [f"target-{index}" for index in range(1, 9)]

        result = _place_ladder_batch_pairwise(
            gateway,
            "BNB",
            levels,
            712.84,
            entry_cloids,
            target_cloids,
        )

        self.assertEqual(len(result), 8)
        self.assertEqual(len(gateway.calls), 8)
        self.assertEqual(gateway.write_checks, ["place complete GALKA safely"])
        for index, call in enumerate(gateway.calls, start=1):
            self.assertEqual(call, ("BNB", index, 712.84, f"entry-{index}", f"target-{index}"))

    def test_bnb_ladder_rounding_builds_all_eight_entries(self):
        levels = build_ladder(712.84, 3000.0, 3)
        self.assertEqual(len(levels), 8)
        self.assertTrue(all(level.price > 0 and level.size > 0 for level in levels))

    def test_install_adds_bnb_near_market_step_and_removes_sol(self):
        install()
        self.assertEqual(CompatibleGalkaLiveEngine._NEAR_MARKET_STEPS["BNB"], 0.01)
        self.assertNotIn("SOL", CompatibleGalkaLiveEngine._NEAR_MARKET_STEPS)


if __name__ == "__main__":
    unittest.main()

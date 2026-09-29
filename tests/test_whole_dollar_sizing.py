from __future__ import annotations

import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from live.live_ladder import LadderLevel
from live.whole_dollar_sizing import (
    SIZING_POLICY,
    TECHNICAL_BUFFER_USD,
    _create_campaign_fast,
    _preview,
)


class _Gateway:
    def __init__(self, account_value: float, withdrawable: float | None = None):
        self.account_value = account_value
        self.withdrawable = account_value if withdrawable is None else withdrawable

    @staticmethod
    def mids():
        return {"BTC": 61_000.0}

    def fresh_account_state(self):
        return {
            "accountValue": self.account_value,
            "withdrawable": self.withdrawable,
            "positions": {},
        }

    @staticmethod
    def preview_ladder(_coin: str, galka_price: float, total_notional: float):
        # Keep the unit test deterministic: no exchange-size rounding. Production
        # uses the real eight-level ladder and therefore may leave a few extra cents.
        entry_price = galka_price * 0.99
        return [
            LadderLevel(
                index=1,
                depth_pct=1.0,
                weight=1.0,
                price=entry_price,
                size=total_notional / entry_price,
                notional=total_notional,
            )
        ]


class _Engine:
    def __init__(self, account_value: float, withdrawable: float | None = None, reserved: float = 0.0):
        self.gateway = _Gateway(account_value, withdrawable)
        self.lock = threading.RLock()
        self.config = SimpleNamespace(
            leverage=10,
            isolated=True,
            maker_fee_rate=0.00015,
            taker_fee_rate=0.00045,
            max_margin_fraction=0.99,
            live_enabled=True,
        )
        self._reserved = reserved

    @staticmethod
    def _coin(value: str) -> str:
        return value.upper()

    def _active_campaigns_locked(self):
        if self._reserved <= 0:
            return []
        return [
            {
                "actualNotional": self._reserved * self.config.leverage,
                "leverage": self.config.leverage,
            }
        ]


class WholeDollarSizingTests(unittest.TestCase):
    def test_301_99_uses_301_whole_dollars(self):
        result = _preview(_Engine(301.99), "BTC", 60_000.0)

        self.assertEqual(result["sizingPolicy"], SIZING_POLICY)
        self.assertEqual(result["wholeDollarCeiling"], 301.0)
        self.assertEqual(result["targetMargin"], 301.0)
        self.assertEqual(result["wholeDollarStepDown"], 0)
        self.assertAlmostEqual(result["requiredMargin"], 301.0)
        self.assertAlmostEqual(result["cashLeftAfterMargin"], 0.99)
        self.assertAlmostEqual(
            result["technicalReserveRequired"],
            3010.0 * 0.00015 + TECHNICAL_BUFFER_USD,
        )

    def test_small_cent_remainder_steps_down_one_dollar_for_entry_fee(self):
        result = _preview(_Engine(301.07), "BTC", 60_000.0)

        self.assertEqual(result["wholeDollarCeiling"], 301.0)
        self.assertEqual(result["targetMargin"], 300.0)
        self.assertEqual(result["wholeDollarStepDown"], 1)
        self.assertAlmostEqual(result["cashLeftAfterMargin"], 1.07)
        self.assertGreaterEqual(result["cashLeftAfterMargin"], result["technicalReserveRequired"])

    def test_other_campaigns_are_reserved_before_whole_dollar_rounding(self):
        result = _preview(_Engine(301.99, reserved=100.0), "BTC", 60_000.0)

        self.assertAlmostEqual(result["reservedMargin"], 100.0)
        self.assertAlmostEqual(result["availableMargin"], 201.99)
        self.assertEqual(result["targetMargin"], 201.0)

    def test_withdrawable_is_a_conservative_upper_bound(self):
        result = _preview(_Engine(301.99, withdrawable=150.80), "BTC", 60_000.0)

        self.assertAlmostEqual(result["availableMargin"], 150.80)
        self.assertEqual(result["targetMargin"], 150.0)
        self.assertAlmostEqual(result["cashLeftAfterMargin"], 0.80)

    def test_legacy_99_percent_create_guard_is_only_raised_to_100_during_create(self):
        engine = _Engine(301.99)
        observed = []

        def fake_create(_self, coin, galka_price, confirmation):
            observed.append(_self.config.max_margin_fraction)
            return {"coin": coin, "galkaPrice": galka_price, "confirmation": confirmation}

        with patch("live.whole_dollar_sizing._ORIGINAL_CREATE_FAST", fake_create):
            result = _create_campaign_fast(engine, "BTC", 60_000.0, "PLACE_REAL_ORDERS")

        self.assertEqual(result["coin"], "BTC")
        self.assertEqual(observed, [1.0])
        self.assertEqual(engine.config.max_margin_fraction, 0.99)


if __name__ == "__main__":
    unittest.main()

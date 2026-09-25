from __future__ import annotations

import threading
import unittest
from types import SimpleNamespace

from live.account_abstraction_compat import AccountModeCompatibleGalkaClassicGateway


class PortfolioInfo:
    def __init__(self):
        self.spot_calls = 0

    def user_state(self, _address):
        return {
            "marginSummary": {
                "accountValue": "296.43",
                "totalMarginUsed": "0",
                "totalNtlPos": "0",
            },
            "withdrawable": "0",
            "assetPositions": [],
        }

    def spot_user_state(self, _address):
        self.spot_calls += 1
        return {
            "balances": [
                {"coin": "USDC", "total": "296.43", "hold": "0.00"},
            ]
        }


class PortfolioMarginBalanceTests(unittest.TestCase):
    def make_gateway(self):
        gateway = AccountModeCompatibleGalkaClassicGateway.__new__(
            AccountModeCompatibleGalkaClassicGateway
        )
        gateway.config = SimpleNamespace(account_address="0x" + "11" * 20)
        gateway._io_lock = threading.RLock()
        gateway._cache_lock = threading.RLock()
        gateway._cache = {}
        gateway._account_mode_value = "portfolioMargin"
        gateway._account_mode_checked_at = 10**18
        gateway.info = PortfolioInfo()
        return gateway

    def test_portfolio_margin_uses_spot_usdc_as_spendable_balance(self):
        gateway = self.make_gateway()
        state = gateway.account_state(fresh=True)

        self.assertEqual(state["accountMode"], "portfolioMargin")
        self.assertAlmostEqual(state["accountValue"], 296.43)
        self.assertAlmostEqual(state["withdrawable"], 296.43)
        self.assertEqual(state["balanceSource"], "spot-usdc-portfolio-margin")
        self.assertEqual(gateway.info.spot_calls, 1)

    def test_portfolio_margin_respects_spot_hold(self):
        gateway = self.make_gateway()
        gateway.info.spot_user_state = lambda _address: {
            "balances": [{"coin": "USDC", "total": "296.43", "hold": "25.00"}]
        }
        state = gateway.account_state(fresh=True)
        self.assertAlmostEqual(state["withdrawable"], 271.43)


if __name__ == "__main__":
    unittest.main()

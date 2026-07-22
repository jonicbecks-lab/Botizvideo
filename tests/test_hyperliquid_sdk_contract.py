from __future__ import annotations

import inspect
import unittest
from importlib.metadata import version

from hyperliquid.exchange import Exchange
from hyperliquid.info import Info


class HyperliquidSdkContractTests(unittest.TestCase):
    def test_audited_sdk_version_is_installed(self):
        self.assertEqual(version("hyperliquid-python-sdk"), "0.24.0")

    def test_exchange_methods_keep_required_parameters(self):
        contracts = {
            Exchange.__init__: {"account_address", "timeout"},
            Exchange.update_leverage: {"leverage", "name", "is_cross"},
            Exchange.bulk_orders: {"order_requests", "grouping"},
            Exchange.order: {"name", "is_buy", "sz", "limit_px", "order_type", "reduce_only", "cloid"},
            Exchange.modify_order: {"oid", "name", "is_buy", "sz", "limit_px", "order_type", "reduce_only", "cloid"},
            Exchange.bulk_cancel: {"cancel_requests"},
            Exchange.market_close: {"coin", "slippage", "cloid"},
        }
        for method, required in contracts.items():
            with self.subTest(method=method.__qualname__):
                self.assertTrue(required.issubset(inspect.signature(method).parameters))

    def test_info_methods_keep_required_parameters(self):
        contracts = {
            Info.__init__: {"skip_ws", "timeout"},
            Info.user_state: {"address"},
            Info.spot_user_state: {"address"},
            Info.frontend_open_orders: {"address"},
            Info.user_fills_by_time: {"address", "start_time"},
            Info.query_order_by_oid: {"user", "oid"},
            Info.candles_snapshot: {"name", "interval", "startTime", "endTime"},
        }
        for method, required in contracts.items():
            with self.subTest(method=method.__qualname__):
                self.assertTrue(required.issubset(inspect.signature(method).parameters))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import threading
import unittest
from types import SimpleNamespace

from live.hyperliquid_gateway import (
    GatewayError,
    HyperliquidGateway,
    _finite_number,
    _parse_user_abstraction,
    _unified_usdc_values,
    _validate_agent_wallet,
)


class FakeExchange:
    def __init__(self):
        self.leverage_response = {"status": "ok", "response": {"type": "default"}}
        self.cancel_response = {
            "status": "ok",
            "response": {"type": "cancel", "data": {"statuses": ["success"]}},
        }
        self.order_response = {
            "status": "ok",
            "response": {"type": "order", "data": {"statuses": [{"resting": {"oid": 55}}]}},
        }
        self.market_close_response = {
            "status": "ok",
            "response": {"type": "order", "data": {"statuses": [{"filled": {"oid": 56}}]}},
        }

    def update_leverage(self, *_args):
        return self.leverage_response

    def bulk_cancel(self, _requests):
        return self.cancel_response

    def order(self, *_args, **_kwargs):
        return self.order_response

    def modify_order(self, *_args, **_kwargs):
        return self.order_response

    def market_close(self, *_args, **_kwargs):
        return self.market_close_response


class CountingInfo:
    def __init__(self):
        self.open_orders_calls = 0

    def frontend_open_orders(self, _address):
        self.open_orders_calls += 1
        return [
            {
                "coin": "BTC",
                "oid": self.open_orders_calls,
                "side": "B",
                "limitPx": "60000",
                "sz": "0.001",
                "origSz": "0.001",
                "reduceOnly": False,
            }
        ]


class HyperliquidGatewayTests(unittest.TestCase):
    def make_gateway(self):
        gateway = HyperliquidGateway.__new__(HyperliquidGateway)
        gateway.config = SimpleNamespace(
            leverage=10,
            isolated=True,
            account_address="0x" + "11" * 20,
            live_enabled=True,
        )
        gateway._universe = {"BTC": {"szDecimals": 5, "maxLeverage": 50}}
        gateway._io_lock = threading.RLock()
        gateway._cache_lock = threading.RLock()
        gateway._cache = {}
        gateway._account_mode_value = "default"
        gateway._account_mode_checked_at = 0.0
        gateway.exchange = FakeExchange()
        gateway.info = CountingInfo()
        return gateway

    def test_parse_user_abstraction_across_response_shapes(self):
        self.assertEqual(_parse_user_abstraction("unifiedAccount"), "unifiedAccount")
        self.assertEqual(_parse_user_abstraction({"abstraction": "unifiedAccount"}), "unifiedAccount")
        self.assertEqual(_parse_user_abstraction({"type": "portfolioMargin"}), "portfolioMargin")
        self.assertEqual(_parse_user_abstraction(None), "default")

    def test_unified_usdc_values_use_total_minus_hold(self):
        account_value, available = _unified_usdc_values(
            {
                "balances": [
                    {"coin": "USDC", "total": "17.97553308", "hold": "0.97553308"},
                    {"coin": "HYPE", "total": "100", "hold": "0"},
                ]
            }
        )
        self.assertAlmostEqual(account_value, 17.97553308)
        self.assertAlmostEqual(available, 17.0)

    def test_unified_usdc_values_never_return_negative_available(self):
        account_value, available = _unified_usdc_values(
            {"balances": [{"coin": "USDC", "total": "1", "hold": "2"}]}
        )
        self.assertEqual(account_value, 1.0)
        self.assertEqual(available, 0.0)

    def test_non_finite_exchange_numbers_are_rejected(self):
        for value in ("nan", "inf", "-inf"):
            with self.subTest(value=value), self.assertRaises(GatewayError):
                _finite_number(value, "test")

    def test_main_account_private_key_is_rejected(self):
        address = "0x" + "12" * 20
        with self.assertRaisesRegex(GatewayError, "main account"):
            _validate_agent_wallet(address.upper(), address)
        _validate_agent_wallet(address, "0x" + "34" * 20)

    def test_leverage_rejection_is_not_treated_as_success(self):
        gateway = self.make_gateway()
        gateway.exchange.leverage_response = {
            "status": "err",
            "response": "insufficient margin",
        }
        with self.assertRaises(GatewayError):
            gateway.set_leverage("BTC")

    def test_cancel_checks_every_per_order_status(self):
        gateway = self.make_gateway()
        gateway.exchange.cancel_response = {
            "status": "ok",
            "response": {
                "type": "cancel",
                "data": {"statuses": [{"error": "order not found"}]},
            },
        }
        with self.assertRaisesRegex(GatewayError, "order not found"):
            gateway.cancel_oids("BTC", [123])

    def test_order_response_rejects_partial_status_list(self):
        gateway = self.make_gateway()
        response = {
            "status": "ok",
            "response": {
                "type": "order",
                "data": {"statuses": [{"resting": {"oid": 1}}]},
            },
        }
        levels = [SimpleNamespace(index=1, price=100.0, size=0.1), None]
        with self.assertRaisesRegex(GatewayError, "incomplete"):
            gateway._parse_order_response(response, levels, ["a", "b"])

    def test_fallback_target_rejects_pending_status(self):
        gateway = self.make_gateway()
        gateway.exchange.order_response = {
            "status": "ok",
            "response": {"type": "order", "data": {"statuses": ["waitingForFill"]}},
        }
        with self.assertRaisesRegex(GatewayError, "not accepted"):
            gateway.place_or_replace_target("BTC", 0.01, 60_000, cloid=None)

    def test_emergency_close_requires_immediate_fill(self):
        gateway = self.make_gateway()
        gateway.exchange.market_close_response = {
            "status": "ok",
            "response": {"type": "order", "data": {"statuses": [{"resting": {"oid": 99}}]}},
        }
        with self.assertRaisesRegex(GatewayError, "not filled immediately"):
            gateway.emergency_market_close("BTC")

    def test_emergency_close_passes_explicit_size_and_bounded_slippage(self):
        gateway = self.make_gateway()
        calls = []

        def market_close(*args, **kwargs):
            calls.append((args, kwargs))
            return {
                "status": "ok",
                "response": {"type": "order", "data": {"statuses": [{"filled": {"oid": 42}}]}},
            }

        gateway.exchange.market_close = market_close
        gateway.emergency_market_close("BTC", size=0.002, slippage=0.05)
        self.assertEqual(calls[0][1]["sz"], 0.002)
        self.assertEqual(calls[0][1]["slippage"], 0.05)
        with self.assertRaisesRegex(GatewayError, "slippage"):
            gateway.emergency_market_close("BTC", size=0.002, slippage=0.11)

    def test_order_response_surfaces_embedded_error(self):
        gateway = self.make_gateway()
        response = {
            "status": "ok",
            "response": {
                "type": "order",
                "data": {"statuses": [{"error": "Price must be divisible by tick size"}]},
            },
        }
        with self.assertRaisesRegex(GatewayError, "tick size"):
            gateway._parse_order_response(response, [None], [None])

    def test_fresh_open_orders_bypasses_short_browser_cache(self):
        gateway = self.make_gateway()
        first = gateway.open_orders("BTC")
        second = gateway.open_orders("BTC")
        fresh = gateway.fresh_open_orders("BTC")
        self.assertEqual(gateway.info.open_orders_calls, 2)
        self.assertEqual(first[0]["oid"], second[0]["oid"])
        self.assertNotEqual(first[0]["oid"], fresh[0]["oid"])

    def test_read_only_config_blocks_every_exchange_write(self):
        gateway = self.make_gateway()
        gateway.config.live_enabled = False
        level = SimpleNamespace(index=1, price=60_000.0, size=0.001)

        actions = [
            lambda: gateway.set_leverage("BTC"),
            lambda: gateway.place_entry_with_target("BTC", level, 61_000.0),
            lambda: gateway.place_or_replace_target("BTC", 0.001, 61_000.0),
            lambda: gateway.cancel_oids("BTC", [123]),
            lambda: gateway.emergency_market_close("BTC"),
        ]
        for action in actions:
            with self.subTest(action=action), self.assertRaisesRegex(GatewayError, "write blocked"):
                action()


if __name__ == "__main__":
    unittest.main()

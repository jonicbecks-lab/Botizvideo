from __future__ import annotations

import unittest

from live import hyperliquid_gateway
from live.galka_v2_server_entry import V2_COINS, install_v2_runtime
from live.hyperliquid_compat import CompatibleGalkaLiveEngine


class GalkaV2RuntimeTests(unittest.TestCase):
    def test_runtime_uses_btc_eth_bnb_and_near_market_bnb_step(self):
        original = set(hyperliquid_gateway.SUPPORTED_COINS)
        try:
            install_v2_runtime()
            self.assertEqual(hyperliquid_gateway.SUPPORTED_COINS, V2_COINS)
            self.assertEqual(V2_COINS, {"BTC", "ETH", "BNB"})
            self.assertIn("BNB", CompatibleGalkaLiveEngine._NEAR_MARKET_STEPS)
            self.assertNotIn("SOL", CompatibleGalkaLiveEngine._NEAR_MARKET_STEPS)
        finally:
            hyperliquid_gateway.SUPPORTED_COINS.clear()
            hyperliquid_gateway.SUPPORTED_COINS.update(original)


if __name__ == "__main__":
    unittest.main()

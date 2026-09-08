import unittest

from live.mem_market_whitelist import MEM_MARKET_ORDER, install_mem_market_whitelist


class _BaseGateway:
    def market_catalog(self):
        return [
            {"name": "BTC"},
            {"name": "PNUT"},
            {"name": "kPEPE"},
            {"name": "CASHCAT"},
            {"name": "DOGE"},
            {"name": "ETH"},
            {"name": "PONS"},
        ]


class MemMarketWhitelistTests(unittest.TestCase):
    def test_catalog_keeps_only_selected_memes_and_requested_order(self):
        gateway_cls = install_mem_market_whitelist(_BaseGateway)
        rows = gateway_cls().market_catalog()
        self.assertEqual(
            [row["name"] for row in rows],
            [name for name in MEM_MARKET_ORDER if name in {"PONS", "CASHCAT", "DOGE", "kPEPE", "PNUT"}],
        )

    def test_non_mem_markets_are_removed(self):
        gateway_cls = install_mem_market_whitelist(_BaseGateway)
        names = {row["name"] for row in gateway_cls().market_catalog()}
        self.assertNotIn("BTC", names)
        self.assertNotIn("ETH", names)


if __name__ == "__main__":
    unittest.main()

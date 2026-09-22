import unittest

from research.flow_lab.parsers import parse_binance_aggtrade, parse_bybit_public_trade, parse_hyperliquid_trade, parse_okx_trade


class ParserTests(unittest.TestCase):
    def test_binance_buyer_maker_means_taker_sell(self):
        e = parse_binance_aggtrade({"e":"aggTrade","s":"BTCUSDT","a":7,"p":"100","q":"2","T":10,"m":True}, "perp")
        self.assertEqual(e.taker_side, "sell")
        self.assertEqual(e.notional_usd, 200.0)

    def test_binance_non_buyer_maker_means_taker_buy(self):
        e = parse_binance_aggtrade({"s":"ETHUSDT","a":8,"p":"10","q":"3","T":11,"m":False}, "spot")
        self.assertEqual(e.taker_side, "buy")

    def test_bybit(self):
        rows = parse_bybit_public_trade({"data":[{"T":12,"s":"ETHUSDT","S":"Buy","v":"4","p":"5","i":"x"}]}, "perp")
        self.assertEqual(rows[0].notional_usd, 20.0)
        self.assertEqual(rows[0].taker_side, "buy")

    def test_okx_spot(self):
        rows = parse_okx_trade({"data":[{"instId":"BTC-USDT","tradeId":"1","px":"100","sz":"0.5","side":"sell","ts":"13"}]}, "spot")
        self.assertEqual(rows[0].qty_base, 0.5)
        self.assertEqual(rows[0].notional_usd, 50.0)

    def test_okx_perp_contract_conversion(self):
        rows = parse_okx_trade({"data":[{"instId":"BTC-USDT-SWAP","tradeId":"1","px":"100","sz":"3","side":"buy","ts":"13"}]}, "perp", {"BTC-USDT-SWAP": 0.01})
        self.assertAlmostEqual(rows[0].qty_base, 0.03)
        self.assertAlmostEqual(rows[0].notional_usd, 3.0)

    def test_okx_perp_refuses_unknown_multiplier(self):
        with self.assertRaises(ValueError):
            parse_okx_trade({"data":[{"instId":"BTC-USDT-SWAP","tradeId":"1","px":"100","sz":"3","side":"buy","ts":"13"}]}, "perp")

    def test_hyperliquid(self):
        rows = parse_hyperliquid_trade({"channel":"trades","data":[{"coin":"BTC","side":"A","px":"100","sz":"0.2","time":14,"tid":99}]})
        self.assertEqual(rows[0].taker_side, "sell")
        self.assertEqual(rows[0].notional_usd, 20.0)


if __name__ == "__main__":
    unittest.main()

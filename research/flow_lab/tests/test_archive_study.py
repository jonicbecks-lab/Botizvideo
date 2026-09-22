import unittest

from research.flow_lab.archive_study import stream_buckets
from research.flow_lab.schema import TradeEvent


def e(ts, side, price=100.0, qty=1.0):
    return TradeEvent("binance", "perp", "BTC", "BTCUSDT", ts, price, qty,
                      price * qty, side, str(ts))


class ArchiveStudyTests(unittest.TestCase):
    def test_stream_buckets(self):
        rows = list(stream_buckets([
            e(1000, "buy", 100, 1),
            e(1500, "sell", 101, 0.5),
            e(31000, "buy", 102, 2),
        ], 30000))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].trade_count, 2)
        self.assertAlmostEqual(rows[0].buy_usd, 100.0)
        self.assertAlmostEqual(rows[0].sell_usd, 50.5)
        self.assertAlmostEqual(rows[0].delta_usd, 49.5)
        self.assertEqual(rows[1].start_ms, 30000)
        self.assertEqual(rows[1].close_price, 102.0)

    def test_reject_unsorted(self):
        with self.assertRaises(ValueError):
            list(stream_buckets([e(2000, "buy"), e(1000, "sell")], 1000))


if __name__ == "__main__":
    unittest.main()

import unittest

from research.flow_lab.metrics import RollingNormalizer, aggregate_events, combine_venues, price_response_features
from research.flow_lab.schema import TradeEvent


def event(exchange, side, notional, ts=1000, price=100, market="perp"):
    qty = notional / price
    return TradeEvent(exchange, market, "BTC", "BTCUSDT", ts, price, qty, notional, side, f"{exchange}-{side}-{ts}-{notional}")


class MetricsTests(unittest.TestCase):
    def test_aggregate_delta_ratio(self):
        b = aggregate_events([event("binance", "buy", 100), event("binance", "sell", 40, 1100)], 1000)[0]
        self.assertEqual(b.delta_usd, 60)
        self.assertEqual(b.gross_usd, 140)
        self.assertAlmostEqual(b.flow_ratio, 60/140)

    def test_combine_without_weights(self):
        buckets = aggregate_events([event("binance", "sell", 100), event("bybit", "sell", 50), event("okx", "buy", 20)], 1000)
        c = combine_venues(buckets)[0]
        self.assertEqual(c["delta_usd"], -130)
        self.assertEqual(c["venue_count"], 3)
        self.assertEqual(c["sell_venue_count"], 2)
        self.assertEqual(c["buy_venue_count"], 1)
        self.assertAlmostEqual(c["consensus"], 1/3)

    def test_normalizer_is_past_only(self):
        n = RollingNormalizer(maxlen=10, min_history=3)
        self.assertIsNone(n.score_then_update(1).zscore)
        n.score_then_update(2)
        n.score_then_update(3)
        score = n.score_then_update(100)
        self.assertEqual(score.history_n, 3)
        self.assertGreater(score.zscore, 10)
        self.assertEqual(score.percentile, 1.0)

    def test_price_response(self):
        f = price_response_features(-1000000, -5, 2000000)
        self.assertEqual(f["response_in_flow_direction_bps"], 5)
        self.assertEqual(f["absolute_flow_ratio"], 0.5)


if __name__ == "__main__":
    unittest.main()

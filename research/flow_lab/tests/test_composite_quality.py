import unittest

from research.flow_lab.composite import EqualVenueComposite
from research.flow_lab.quality import StreamHealth, quality_gate
from research.flow_lab.schema import FlowBucket


def bucket(exchange: str, start_ms: int, delta: float, gross: float = 100.0) -> FlowBucket:
    buy = (gross + delta) / 2
    sell = (gross - delta) / 2
    return FlowBucket(
        exchange=exchange, market="perp", asset="BTC", start_ms=start_ms,
        end_ms=start_ms + 1000, trade_count=1, buy_usd=buy, sell_usd=sell,
        delta_usd=delta, gross_usd=gross, flow_ratio=delta / gross,
        open_price=100.0, close_price=100.0, high_price=100.0, low_price=100.0,
        return_bps=0.0,
    )


class CompositeTests(unittest.TestCase):
    def test_equal_venue_normalization_prevents_raw_volume_weighting(self):
        c = EqualVenueComposite(min_history=2)
        c.combine([bucket("a", 0, 10), bucket("b", 0, 10)])
        c.combine([bucket("a", 1000, -10), bucket("b", 1000, -10)])
        rows = c.combine([
            bucket("a", 2000, 90, gross=100.0),
            bucket("b", 2000, -9000, gross=10000.0),
        ])
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["normalized_venue_count"], 2)
        self.assertEqual(row["buy_venue_count"], 1)
        self.assertEqual(row["sell_venue_count"], 1)
        self.assertEqual(row["sign_consensus"], 0.0)
        self.assertAlmostEqual(row["equal_venue_zscore"], 0.0, places=7)

    def test_current_observation_not_in_own_history(self):
        c = EqualVenueComposite(min_history=2)
        self.assertIsNone(c.combine([bucket("a", 0, 10)])[0]["equal_venue_zscore"])
        self.assertIsNone(c.combine([bucket("a", 1000, -10)])[0]["equal_venue_zscore"])
        self.assertIsNotNone(c.combine([bucket("a", 2000, 50)])[0]["equal_venue_zscore"])


class QualityGateTests(unittest.TestCase):
    def test_pass_when_required_streams_fresh(self):
        streams = {"binance": StreamHealth("binance"), "bybit": StreamHealth("bybit")}
        streams["binance"].observe(9_900, 10_000)
        streams["bybit"].observe(9_950, 10_000)
        row = quality_gate(streams, ("binance", "bybit"), now_ms=10_100)
        self.assertTrue(row["quality_pass"])
        self.assertEqual(row["healthy_stream_count"], 2)

    def test_rejects_missing_stale_lagged_and_gap(self):
        streams = {"binance": StreamHealth("binance"), "bybit": StreamHealth("bybit")}
        streams["binance"].observe(1_000, 10_000)
        streams["bybit"].observe(9_900, 10_000)
        streams["bybit"].sequence_gap()
        row = quality_gate(
            streams, ("binance", "bybit", "okx"), now_ms=20_001,
            max_stale_ms=5_000, max_event_lag_ms=5_000,
        )
        self.assertFalse(row["quality_pass"])
        self.assertIn("missing:okx", row["reasons"])
        self.assertIn("stale:binance", row["reasons"])
        self.assertIn("lagged:binance", row["reasons"])
        self.assertIn("sequence_gap:bybit", row["reasons"])


if __name__ == "__main__":
    unittest.main()

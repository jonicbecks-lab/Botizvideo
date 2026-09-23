import unittest

from research.flow_lab.pattern_engine import OIWindow, PatternEngine
from research.flow_lab.quality import StreamHealth
from research.flow_lab.schema import FlowBucket


def bucket(exchange: str, market: str, start: int, ratio: float) -> FlowBucket:
    gross = 1000.0
    delta = ratio * gross
    buy = (gross + delta) / 2.0
    sell = (gross - delta) / 2.0
    return FlowBucket(
        exchange=exchange, market=market, asset="BTC",
        start_ms=start, end_ms=start + 30_000, trade_count=10,
        buy_usd=buy, sell_usd=sell, delta_usd=delta, gross_usd=gross,
        flow_ratio=ratio, open_price=100.0, close_price=100.0,
        high_price=100.0, low_price=100.0, return_bps=0.0,
    )


def health(now: int) -> dict[str, StreamHealth]:
    h = StreamHealth("trades")
    h.observe(now - 100, now - 50)
    return {"trades": h}


class PatternEngineTests(unittest.TestCase):
    def test_quality_gate_suppresses_events_and_history_updates(self):
        engine = PatternEngine(min_history=2)
        result = engine.evaluate(
            now_ms=10_000,
            stream_health={},
            required_streams=("trades",),
            perp_buckets=[bucket("binance", "perp", 0, 1.0)],
            oi_windows=[OIWindow("binance", "BTC", 1000.0, 2.0, 500.0)],
        )
        self.assertFalse(result["quality"]["quality_pass"])
        self.assertEqual(result["events"], [])
        self.assertEqual(len(engine.perp_composite._normalizers), 0)

    def test_oi_context_is_emitted_under_healthy_gate(self):
        now = 10_000
        engine = PatternEngine(min_history=2)
        result = engine.evaluate(
            now_ms=now,
            stream_health=health(now),
            required_streams=("trades",),
            oi_windows=[OIWindow("binance", "BTC", 1000.0, 2.0, 500.0)],
        )
        labels = {e["pattern_label"] for e in result["events"]}
        self.assertIn("new_longs_building", labels)

    def test_equal_weight_cross_venue_consensus_after_past_warmup(self):
        now = 100_000
        engine = PatternEngine(min_history=2)
        venues = ("binance", "bybit", "okx")
        for i, ratio in enumerate((-0.5, 0.0)):
            engine.evaluate(
                now_ms=now + i,
                stream_health=health(now + i),
                required_streams=("trades",),
                perp_buckets=[bucket(v, "perp", i * 30_000, ratio) for v in venues],
            )
        result = engine.evaluate(
            now_ms=now + 2,
            stream_health=health(now + 2),
            required_streams=("trades",),
            perp_buckets=[bucket(v, "perp", 60_000, 1.0) for v in venues],
        )
        labels = {e["pattern_label"] for e in result["events"]}
        self.assertIn("cross_venue_consensus_buy", labels)


if __name__ == "__main__":
    unittest.main()

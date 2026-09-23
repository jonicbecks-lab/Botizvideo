import unittest

from research.flow_lab.runtime_health import RuntimeHealthRegistry, trade_stream_id
from research.flow_lab.schema import TradeEvent


def event(asset="BTC", recv=1000, ts=900):
    return TradeEvent(
        exchange="binance", market="perp", asset=asset, symbol=f"{asset}USDT",
        ts_ms=ts, price=100.0, qty_base=1.0, notional_usd=100.0,
        taker_side="buy", trade_id=f"{asset}:{ts}", recv_ts_ms=recv,
    )


class RuntimeHealthTests(unittest.TestCase):
    def test_trade_observation_and_duplicate(self):
        h = RuntimeHealthRegistry()
        stream_id = h.observe_trade(event())
        h.observe_trade(event(), duplicate=True)
        self.assertEqual(stream_id, trade_stream_id("binance", "perp", "BTC"))
        self.assertEqual(h.streams[stream_id].event_count, 2)
        self.assertEqual(h.streams[stream_id].duplicate_count, 1)

    def test_reconnect_marks_each_asset(self):
        h = RuntimeHealthRegistry()
        h.reconnect_trade("bybit", "spot", ["BTC", "ETH"])
        self.assertEqual(h.streams[trade_stream_id("bybit", "spot", "BTC")].reconnect_count, 1)
        self.assertEqual(h.streams[trade_stream_id("bybit", "spot", "ETH")].reconnect_count, 1)

    def test_snapshot_uses_quality_gate(self):
        h = RuntimeHealthRegistry()
        h.observe_trade(event("BTC", recv=1000, ts=950))
        required = (trade_stream_id("binance", "perp", "BTC"),)
        row = h.snapshot(required_streams=required, now_ms=1100)
        self.assertTrue(row["quality_pass"])
        row = h.snapshot(required_streams=required, now_ms=7001, max_stale_ms=5000)
        self.assertFalse(row["quality_pass"])
        self.assertIn(f"stale:{required[0]}", row["reasons"])


if __name__ == "__main__":
    unittest.main()

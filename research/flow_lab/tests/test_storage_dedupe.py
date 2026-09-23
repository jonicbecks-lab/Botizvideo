import json
import tempfile
import unittest
from pathlib import Path

from research.flow_lab.dedupe import TradeDeduper
from research.flow_lab.schema import TradeEvent
from research.flow_lab.storage import AsyncJsonlDirectory, AsyncJsonlWriter


def trade(trade_id: str, exchange: str = "binance", symbol: str = "BTCUSDT") -> TradeEvent:
    return TradeEvent(
        exchange=exchange, market="perp", asset="BTC", symbol=symbol,
        ts_ms=1, price=100.0, qty_base=1.0, notional_usd=100.0,
        taker_side="buy", trade_id=trade_id, recv_ts_ms=2,
    )


class DeduperTests(unittest.TestCase):
    def test_duplicate_is_rejected_within_same_stream_identity(self):
        d = TradeDeduper(max_keys=3)
        self.assertTrue(d.accept(trade("1")))
        self.assertFalse(d.accept(trade("1")))
        self.assertTrue(d.accept(trade("1", exchange="bybit")))
        self.assertEqual(d.stats()["duplicate_count"], 1)

    def test_lru_is_bounded(self):
        d = TradeDeduper(max_keys=2)
        self.assertTrue(d.accept(trade("1")))
        self.assertTrue(d.accept(trade("2")))
        self.assertTrue(d.accept(trade("3")))
        self.assertEqual(d.stats()["current_keys"], 2)
        self.assertEqual(d.stats()["evicted_count"], 1)
        self.assertTrue(d.accept(trade("1")))


class StorageTests(unittest.IsolatedAsyncioTestCase):
    async def test_writer_flushes_all_rows_on_close(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "events.jsonl"
            writer = AsyncJsonlWriter(path, batch_size=3, flush_interval_sec=60.0)
            await writer.start()
            for i in range(7):
                await writer.write({"i": i})
            await writer.close()
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([r["i"] for r in rows], list(range(7)))
            self.assertEqual(writer.stats()["written_count"], 7)

    async def test_directory_separates_streams(self):
        with tempfile.TemporaryDirectory() as td:
            sink = AsyncJsonlDirectory(td, batch_size=10, flush_interval_sec=60.0)
            await sink.write("books", {"x": 1})
            await sink.write("context", {"x": 2})
            await sink.close()
            books = json.loads((Path(td) / "books.jsonl").read_text().strip())
            ctx = json.loads((Path(td) / "context.jsonl").read_text().strip())
            self.assertEqual(books["x"], 1)
            self.assertEqual(ctx["x"], 2)


if __name__ == "__main__":
    unittest.main()

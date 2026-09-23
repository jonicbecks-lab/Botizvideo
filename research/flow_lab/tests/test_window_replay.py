import json
from pathlib import Path
import tempfile
import unittest

from research.flow_lab.window_replay import build_pattern_dataset


class SynchronizedReplayTests(unittest.TestCase):
    def test_builds_independent_spot_and_perp_response_families(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            trades = root / "live-trades.jsonl"
            micro = root / "micro"
            micro.mkdir()
            output = root / "out"

            sources = [
                ("binance", "spot"), ("bybit", "spot"), ("okx", "spot"),
                ("binance", "perp"), ("bybit", "perp"), ("okx", "perp"),
                ("hyperliquid", "perp"),
            ]
            notionals = (10.0, 20.0, 100.0, 120.0)
            with trades.open("w", encoding="utf-8") as f:
                for i, notional in enumerate(notionals):
                    start = i * 30_000
                    ts = start + 29_000
                    for j, (exchange, market) in enumerate(sources):
                        row = {
                            "exchange": exchange,
                            "market": market,
                            "asset": "BTC",
                            "symbol": "BTCUSDT",
                            "ts_ms": ts,
                            "price": 100.0,
                            "qty_base": notional / 100.0,
                            "notional_usd": notional,
                            "taker_side": "buy",
                            "trade_id": f"{i}-{j}",
                            "recv_ts_ms": ts + 10,
                        }
                        f.write(json.dumps(row) + "\n")

            summary = build_pattern_dataset(
                trades_path=trades,
                micro_dir=micro,
                output_dir=output,
                window_sec=30,
                min_history=2,
                extreme_percentile=0.99,
            )

            self.assertEqual(summary["quality_pass_windows_by_asset"]["BTC"], 4)
            self.assertEqual(summary["quality_reject_windows_by_asset"].get("BTC", 0), 0)
            self.assertEqual(summary["pattern_event_counts_raw"]["absorption"], 4)
            self.assertEqual(summary["pattern_event_counts_independent"]["absorption"], 2)

            events = [json.loads(line) for line in (output / "pattern_events.jsonl").read_text().splitlines()]
            independent = [e for e in events if e["pattern_family"] == "absorption" and e["is_independent_event"]]
            self.assertEqual({e["market"] for e in independent}, {"spot", "perp"})

    def test_missing_stream_fails_quality_without_warming_patterns(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            trades = root / "live-trades.jsonl"
            micro = root / "micro"
            micro.mkdir()
            output = root / "out"
            with trades.open("w", encoding="utf-8") as f:
                # One Binance stream alone must not qualify as a multi-source window.
                for i in range(3):
                    ts = i * 30_000 + 29_000
                    f.write(json.dumps({
                        "exchange": "binance", "market": "perp", "asset": "BTC",
                        "symbol": "BTCUSDT", "ts_ms": ts, "price": 100.0,
                        "qty_base": 1.0, "notional_usd": 100.0,
                        "taker_side": "buy", "trade_id": str(i), "recv_ts_ms": ts + 10,
                    }) + "\n")

            summary = build_pattern_dataset(
                trades_path=trades,
                micro_dir=micro,
                output_dir=output,
                window_sec=30,
                min_history=2,
            )
            self.assertEqual(summary["quality_pass_windows_by_asset"].get("BTC", 0), 0)
            self.assertEqual(summary["quality_reject_windows_by_asset"]["BTC"], 3)
            self.assertEqual(summary["event_count_raw"], 0)


if __name__ == "__main__":
    unittest.main()

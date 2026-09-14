from __future__ import annotations

import time
import unittest
from types import SimpleNamespace

from live.cluster_volume import ClusterVolumeService


class ClusterVolumeTests(unittest.TestCase):
    def setUp(self):
        self.service = ClusterVolumeService(SimpleNamespace(mainnet=True))
        self.now = int(time.time() * 1000)

    def trade(self, *, tid: int, px: float, sz: float, side: str, offset_ms: int = 0):
        self.service._ingest_trade(
            {
                "coin": "BTC",
                "side": side,
                "px": str(px),
                "sz": str(sz),
                "time": self.now + offset_ms,
                "tid": tid,
                "hash": f"hash-{tid}",
            }
        )

    def test_buy_sell_notional_and_delta_are_aggregated(self):
        self.trade(tid=1, px=100_000, sz=0.10, side="B")
        self.trade(tid=2, px=100_001, sz=0.05, side="A")
        payload = self.service.snapshot("BTC", "5m", "normal")
        self.assertEqual(payload["coin"], "BTC")
        self.assertEqual(payload["interval"], "5m")
        self.assertEqual(len(payload["cells"]), 1)
        cell = payload["cells"][0]
        self.assertAlmostEqual(cell["buyNotional"], 10_000.0, places=6)
        self.assertAlmostEqual(cell["sellNotional"], 5_000.05, places=6)
        self.assertAlmostEqual(cell["deltaNotional"], 4_999.95, places=6)
        self.assertEqual(cell["tradeCount"], 2)

    def test_trade_id_deduplicates_reconnect_snapshot(self):
        self.trade(tid=9, px=100_000, sz=0.10, side="B")
        self.trade(tid=9, px=100_000, sz=0.10, side="B")
        payload = self.service.snapshot("BTC", "1m", "fine")
        self.assertEqual(sum(row["tradeCount"] for row in payload["cells"]), 1)
        self.assertAlmostEqual(sum(row["totalNotional"] for row in payload["cells"]), 10_000.0)

    def test_price_aggregation_changes_cluster_count(self):
        self.trade(tid=1, px=100_001, sz=0.10, side="B")
        self.trade(tid=2, px=100_018, sz=0.10, side="B")
        fine = self.service.snapshot("BTC", "5m", "fine")
        coarse = self.service.snapshot("BTC", "5m", "coarse")
        self.assertGreaterEqual(len(fine["cells"]), len(coarse["cells"]))
        self.assertAlmostEqual(
            sum(row["totalNotional"] for row in fine["cells"]),
            sum(row["totalNotional"] for row in coarse["cells"]),
            places=6,
        )


if __name__ == "__main__":
    unittest.main()

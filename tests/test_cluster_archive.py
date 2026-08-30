from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from live.cluster_archive import PersistentClusterVolumeService


class PersistentClusterVolumeTests(unittest.TestCase):
    def make_service(self, root: Path) -> PersistentClusterVolumeService:
        return PersistentClusterVolumeService(
            SimpleNamespace(mainnet=True, data_dir=root)
        )

    def test_completed_cluster_cell_is_readable_from_archive_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = self.make_service(root)
            now = int(time.time() * 1000)
            trade_time = (now // 60_000 * 60_000) - 120_000 + 10_000
            service._ingest_trade(
                {
                    "coin": "BTC",
                    "px": "65000",
                    "sz": "0.25",
                    "time": trade_time,
                    "side": "B",
                    "tid": 101,
                }
            )

            archived = list((root / "research" / "clusters" / "archive" / "BTC").glob("*.jsonl"))
            self.assertTrue(archived)

            service._cells["BTC"].clear()
            result = service.snapshot(
                "BTC",
                "5m",
                "auto",
                trade_time - 60_000,
                trade_time + 60_000,
            )
            self.assertEqual(len(result["cells"]), 1)
            self.assertAlmostEqual(result["cells"][0]["totalNotional"], 16_250.0)
            self.assertAlmostEqual(result["cells"][0]["buyNotional"], 16_250.0)
            self.assertTrue(result["archivePersistent"])

    def test_current_minute_checkpoint_survives_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = self.make_service(root)
            now = int(time.time() * 1000)
            trade_time = now // 60_000 * 60_000 + 5_000
            service._ingest_trade(
                {
                    "coin": "ETH",
                    "px": "1900",
                    "sz": "2",
                    "time": trade_time,
                    "side": "A",
                    "tid": 202,
                }
            )
            service._checkpoint_coin("ETH", force=True)

            restored = self.make_service(root)
            minute = trade_time // 60_000 * 60_000
            self.assertTrue(
                any(key[0] == minute for key in restored._cells["ETH"])
            )
            result = restored.snapshot(
                "ETH",
                "1m",
                "fine",
                minute,
                minute + 59_999,
            )
            self.assertEqual(len(result["cells"]), 1)
            self.assertAlmostEqual(result["cells"][0]["sellNotional"], 3_800.0)


if __name__ == "__main__":
    unittest.main()

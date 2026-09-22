import tempfile
import unittest
import zipfile
from datetime import date
from pathlib import Path

from research.flow_lab.historical import binance_vision_aggtrades_url, iter_binance_aggtrades_zip


class HistoricalTests(unittest.TestCase):
    def test_url(self):
        u = binance_vision_aggtrades_url("BTCUSDT", date(2026, 9, 21), "perp")
        self.assertEqual(u, "https://data.binance.vision/data/futures/um/daily/aggTrades/BTCUSDT/BTCUSDT-aggTrades-2026-09-21.zip")

    def test_parse_headerless_zip(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x.zip"
            with zipfile.ZipFile(p, "w") as z:
                z.writestr("x.csv", "1,100,2,10,11,1700000000000,true\n2,101,3,12,13,1700000000100,false\n")
            rows = list(iter_binance_aggtrades_zip(p, "BTCUSDT", "perp"))
            self.assertEqual(rows[0].taker_side, "sell")
            self.assertEqual(rows[0].notional_usd, 200.0)
            self.assertEqual(rows[1].taker_side, "buy")

    def test_parse_header_and_microseconds(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x.zip"
            with zipfile.ZipFile(p, "w") as z:
                z.writestr("x.csv", "agg_trade_id,price,quantity,first_trade_id,last_trade_id,transact_time,is_buyer_maker\n1,2000,0.5,1,1,1700000000000000,false\n")
            row = next(iter_binance_aggtrades_zip(p, "ETHUSDT", "spot"))
            self.assertEqual(row.ts_ms, 1700000000000)
            self.assertEqual(row.asset, "ETH")
            self.assertEqual(row.notional_usd, 1000.0)


if __name__ == "__main__":
    unittest.main()

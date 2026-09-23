import unittest

from research.flow_lab.book import (
    BookSnapshot, MutableBook, apply_bybit_orderbook, book_features,
    depth_change_features, parse_hyperliquid_l2book, parse_okx_books_snapshot,
    replenishment_proxy,
)
from research.flow_lab.context import (
    parse_bybit_liquidations, parse_bybit_ticker, parse_hyperliquid_asset_ctx,
    parse_okx_funding,
)
from research.flow_lab.event_study import (
    EventStudyConfig, assign_event_families, build_event_table, classify_response,
    classify_response_legacy, classify_response_vol_normalized, independent_events,
)


class BookTests(unittest.TestCase):
    def test_bybit_snapshot_delta(self):
        b = MutableBook()
        snap = apply_bybit_orderbook({"topic":"orderbook.50.BTCUSDT","type":"snapshot","ts":1,
            "data":{"s":"BTCUSDT","u":10,"b":[["100","2"],["99","1"]],"a":[["101","3"]]}}, "perp", b)
        self.assertEqual(snap.bids[0], (100.0, 2.0))
        cur = apply_bybit_orderbook({"topic":"orderbook.50.BTCUSDT","type":"delta","ts":2,
            "data":{"s":"BTCUSDT","u":11,"b":[["100","3"],["99","0"]],"a":[["101","2"]]}}, "perp", b)
        self.assertEqual(cur.bids, ((100.0, 3.0),))
        self.assertEqual(cur.asks[0], (101.0, 2.0))

    def test_book_features_and_refill_proxy(self):
        p = BookSnapshot("x","perp","BTC","BTC",1,((100,1),(99.95,1)),((100.1,1),))
        c = BookSnapshot("x","perp","BTC","BTC",2,((100,3),(99.95,1)),((100.1,1),))
        f = book_features(c, (10,))
        self.assertTrue(f["book_valid"])
        self.assertGreater(f["bid_depth_usd_10bps"], f["ask_depth_usd_10bps"])
        d = depth_change_features(p, c, 10)
        self.assertAlmostEqual(d["bid_add_usd"], 200.0)
        r = replenishment_proxy(-1000, d)
        self.assertAlmostEqual(r["bid_refill_vs_sell_flow"], 0.2)

    def test_okx_contract_multiplier(self):
        rows = parse_okx_books_snapshot({"arg":{"channel":"books5","instId":"BTC-USDT-SWAP"},
            "data":[{"ts":"5","bids":[["100","2","0","1"]],"asks":[["101","3","0","1"]]}]},
            "perp", qty_multiplier=0.01)
        self.assertEqual(rows[0].bids[0], (100.0, 0.02))

    def test_hyperliquid_book(self):
        row = parse_hyperliquid_l2book({"channel":"l2Book","data":{"coin":"ETH","time":8,
            "levels":[[{"px":"10","sz":"2","n":1}],[{"px":"11","sz":"3","n":1}]]}})
        self.assertEqual(row.asset, "ETH")
        self.assertEqual(row.asks[0], (11.0, 3.0))


class ContextTests(unittest.TestCase):
    def test_bybit_ticker(self):
        c = parse_bybit_ticker({"topic":"tickers.BTCUSDT","ts":10,"data":{"symbol":"BTCUSDT",
            "markPrice":"100","openInterest":"5","openInterestValue":"500","fundingRate":"0.0001"}})
        self.assertEqual(c.open_interest_usd, 500.0)
        self.assertEqual(c.funding_rate, 0.0001)

    def test_bybit_liquidation_semantics(self):
        rows = parse_bybit_liquidations({"topic":"allLiquidation.BTCUSDT","data":[
            {"T":11,"s":"BTCUSDT","S":"Buy","v":"2","p":"100"},
            {"T":12,"s":"BTCUSDT","S":"Sell","v":"3","p":"100"}]})
        self.assertEqual(rows[0].liquidated_side, "long")
        self.assertEqual(rows[1].liquidated_side, "short")
        self.assertEqual(rows[1].notional_usd, 300.0)

    def test_hyperliquid_ctx(self):
        c = parse_hyperliquid_asset_ctx({"channel":"activeAssetCtx","data":{"coin":"ETH","ctx":{
            "markPx":"2000","openInterest":"100","funding":"0.00002"}}}, recv_ts_ms=13)
        self.assertEqual(c.open_interest_usd, 200000.0)

    def test_okx_funding(self):
        c = parse_okx_funding({"arg":{"channel":"funding-rate","instId":"BTC-USDT-SWAP"},
            "data":[{"instId":"BTC-USDT-SWAP","fundingRate":"0.0003","ts":"14"}]})
        self.assertEqual(c.funding_rate, 0.0003)


class EventStudyTests(unittest.TestCase):
    def test_classification(self):
        self.assertEqual(classify_response(-100, -1), "absorption_candidate")
        self.assertEqual(classify_response(-100, -10), "continuation_candidate")
        self.assertEqual(classify_response(100, 4), "intermediate")
        self.assertEqual(classify_response(100, -10), "instant_reversal")
        self.assertEqual(classify_response_legacy(100, -10), "absorption_candidate")

    def test_vol_normalized_classification(self):
        klass, units = classify_response_vol_normalized(100, 1.0, 4.0)
        self.assertEqual(klass, "absorption_candidate")
        self.assertAlmostEqual(units, 0.25)
        klass, units = classify_response_vol_normalized(-100, 8.0, 4.0)
        self.assertEqual(klass, "instant_reversal")
        self.assertAlmostEqual(units, -2.0)
        klass, units = classify_response_vol_normalized(-100, -8.0, 4.0)
        self.assertEqual(klass, "continuation_candidate")
        self.assertAlmostEqual(units, 2.0)

    def test_event_family_is_causal_first_event(self):
        rows = assign_event_families([
            {"_bucket_index": 10, "asset":"BTC", "market":"perp", "delta_usd":100},
            {"_bucket_index": 12, "asset":"BTC", "market":"perp", "delta_usd":200},
            {"_bucket_index": 17, "asset":"BTC", "market":"perp", "delta_usd":300},
            {"_bucket_index": 18, "asset":"BTC", "market":"perp", "delta_usd":-300},
        ], cooldown_buckets=4)
        self.assertTrue(rows[0]["is_independent_event"])
        self.assertFalse(rows[1]["is_independent_event"])
        self.assertTrue(rows[2]["is_independent_event"])
        self.assertTrue(rows[3]["is_independent_event"])
        self.assertEqual(len(independent_events(rows)), 3)

    def test_past_only_extreme_and_future_labels(self):
        composite = []
        ref = []
        deltas = [1, 2, 3, 100, 2, 2]
        returns = [1.0, 2.0, 1.0, -1.0, 1.0, 1.0]
        for i, d in enumerate(deltas):
            composite.append({"start_ms":i*1000,"end_ms":i*1000+1000,"delta_usd":d,
                              "gross_usd":abs(d)+1,"flow_ratio":0.5,"market":"perp","asset":"BTC"})
            ref.append({"start_ms":i*1000,"close_price":100+i,"return_bps":returns[i]})
        cfg = EventStudyConfig(extreme_percentile=0.99, min_history=3, horizons_buckets=(1,))
        rows = build_event_table(composite, ref, cfg)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["start_ms"], 3000)
        self.assertIsNotNone(rows[0]["future_1b_raw_bps"])
        self.assertGreater(rows[0]["abs_delta_zscore_past_only"], 10)
        self.assertIsNotNone(rows[0]["impact_scale_bps_past_only"])
        self.assertIn("response_class_legacy", rows[0])
        self.assertTrue(rows[0]["is_independent_event"])


if __name__ == "__main__":
    unittest.main()

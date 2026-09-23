import unittest

from research.flow_lab.catalog_study import (
    _bh_qvalues,
    attach_catalog_forward_labels,
    day_block_signflip_p,
    summarize_catalog,
)


class CatalogStudyTests(unittest.TestCase):
    def test_forward_labels_and_close_path_mae_mfe(self):
        windows = []
        closes = [100.0, 101.0, 99.0, 103.0]
        for i, close in enumerate(closes):
            windows.append({
                "asset": "BTC",
                "start_ms": i * 30_000,
                "end_ms": (i + 1) * 30_000,
                "response_observations": {
                    "perp": {"reference_close": close},
                },
            })
        events = [{
            "asset": "BTC", "market": "perp", "start_ms": 0,
            "pattern_family": "continuation", "pattern_label": "continuation",
            "hypothesis_direction": 1, "is_independent_event": True,
        }]
        labeled = attach_catalog_forward_labels(events, windows, (1, 2, 3))
        e = labeled[0]
        self.assertAlmostEqual(e["future_1b_raw_bps"], 100.0)
        self.assertAlmostEqual(e["future_2b_raw_bps"], -100.0)
        self.assertAlmostEqual(e["future_3b_hypothesis_bps"], 300.0)
        self.assertAlmostEqual(e["future_3b_close_path_mfe_bps"], 300.0)
        self.assertAlmostEqual(e["future_3b_close_path_mae_bps"], 100.0)

    def test_summary_uses_only_independent_events_for_primary_metrics(self):
        rows = [
            {
                "asset": "ETH", "market": "perp", "start_ms": 0,
                "pattern_family": "continuation", "pattern_label": "continuation",
                "is_independent_event": True,
                "future_1b_raw_bps": 10.0, "future_1b_hypothesis_bps": 10.0,
                "future_1b_close_path_mfe_bps": 12.0, "future_1b_close_path_mae_bps": 2.0,
            },
            {
                "asset": "ETH", "market": "perp", "start_ms": 30_000,
                "pattern_family": "continuation", "pattern_label": "continuation",
                "is_independent_event": False,
                "future_1b_raw_bps": -100.0, "future_1b_hypothesis_bps": -100.0,
                "future_1b_close_path_mfe_bps": 0.0, "future_1b_close_path_mae_bps": 100.0,
            },
        ]
        summary = summarize_catalog(rows, horizons_buckets=(1,), bootstrap_draws=10, signflip_draws=10)
        self.assertEqual(len(summary), 1)
        r = summary[0]
        self.assertEqual(r["raw_event_count"], 2)
        self.assertEqual(r["independent_event_count"], 1)
        self.assertEqual(r["h1_mean_hypothesis_bps"], 10.0)
        self.assertEqual(r["h1_cost_2p0_mean_net_bps"], 8.0)

    def test_exact_day_block_signflip_and_bh(self):
        p = day_block_signflip_p({"2026-09-01": [10.0], "2026-09-02": [10.0]})
        self.assertEqual(p, 0.5)
        q = _bh_qvalues([((0, "h1"), 0.01), ((1, "h1"), 0.04), ((2, "h1"), 0.2)])
        self.assertAlmostEqual(q[(0, "h1")], 0.03)
        self.assertAlmostEqual(q[(1, "h1")], 0.06)
        self.assertAlmostEqual(q[(2, "h1")], 0.2)


if __name__ == "__main__":
    unittest.main()

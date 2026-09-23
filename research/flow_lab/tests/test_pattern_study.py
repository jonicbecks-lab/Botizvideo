import unittest

from research.flow_lab.pattern_study import attach_future_returns, summarize_pattern_events


class PatternStudyTests(unittest.TestCase):
    def test_same_forward_labeling_for_every_pattern(self):
        refs = [
            {"start_ms": 0, "close_price": 100.0},
            {"start_ms": 1, "close_price": 101.0},
            {"start_ms": 2, "close_price": 99.0},
        ]
        events = [{
            "pattern_family": "continuation",
            "pattern_label": "continuation",
            "start_ms": 0,
            "hypothesis_direction": 1,
        }]
        labeled = attach_future_returns(events, refs, (1, 2))
        self.assertAlmostEqual(labeled[0]["future_1b_raw_bps"], 100.0)
        self.assertAlmostEqual(labeled[0]["future_2b_raw_bps"], -100.0)
        self.assertAlmostEqual(labeled[0]["future_1b_hypothesis_bps"], 100.0)

    def test_summary_keeps_pattern_families_separate(self):
        events = [
            {
                "pattern_family": "continuation", "pattern_label": "continuation",
                "future_1b_raw_bps": 10.0, "future_1b_hypothesis_bps": 10.0,
                "is_independent_event": True,
            },
            {
                "pattern_family": "instant_reversal", "pattern_label": "instant_reversal",
                "future_1b_raw_bps": -8.0, "future_1b_hypothesis_bps": 8.0,
                "is_independent_event": True,
            },
        ]
        rows = summarize_pattern_events(events, (1,))
        self.assertEqual(len(rows), 2)
        by_family = {r["pattern_family"]: r for r in rows}
        self.assertEqual(by_family["continuation"]["h1_hypothesis_hit_rate"], 1.0)
        self.assertEqual(by_family["instant_reversal"]["h1_hypothesis_hit_rate"], 1.0)


if __name__ == "__main__":
    unittest.main()

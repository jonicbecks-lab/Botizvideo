import unittest

from research.flow_lab.event_study import EventStudyConfig
from research.flow_lab.families import PatternFamilyClusterer
from research.flow_lab.online_response import OnlineResponseTracker


class OnlinePatternTests(unittest.TestCase):
    def test_response_tracker_scores_before_update_and_clusters_causally(self):
        cfg = EventStudyConfig(min_history=2, extreme_percentile=0.99, family_cooldown_buckets=4)
        tracker = OnlineResponseTracker(cfg, bucket_ms=30_000)

        def observe(i, delta):
            return tracker.observe(
                {
                    "asset": "BTC", "market": "perp",
                    "start_ms": i * 30_000, "end_ms": (i + 1) * 30_000,
                    "delta_usd": delta, "gross_usd": abs(delta),
                    "flow_ratio": 1.0, "venue_count": 4,
                },
                reference_return_bps=0.0,
                reference_close=100.0,
            )

        self.assertIsNone(observe(0, 10.0)["response_event"])
        self.assertIsNone(observe(1, 20.0)["response_event"])
        third = observe(2, 100.0)
        self.assertEqual(third["abs_delta_percentile_past_only"], 1.0)
        self.assertEqual(third["response_class"], "absorption_candidate")
        self.assertTrue(third["response_event"]["is_independent_event"])
        fourth = observe(3, 120.0)
        self.assertFalse(fourth["response_event"]["is_independent_event"])
        self.assertEqual(
            fourth["response_event"]["event_family_id"],
            third["response_event"]["event_family_id"],
        )

    def test_catalog_family_keeps_spot_and_perp_separate(self):
        cluster = PatternFamilyClusterer(bucket_ms=30_000, cooldown_buckets=4)
        spot = cluster.observe({
            "asset": "ETH", "market": "spot", "start_ms": 60_000,
            "pattern_family": "absorption", "hypothesis_direction": -1,
        })
        perp = cluster.observe({
            "asset": "ETH", "market": "perp", "start_ms": 60_000,
            "pattern_family": "absorption", "hypothesis_direction": -1,
        })
        self.assertTrue(spot["is_independent_event"])
        self.assertTrue(perp["is_independent_event"])
        self.assertNotEqual(spot["event_family_id"], perp["event_family_id"])

    def test_catalog_family_first_event_is_representative(self):
        cluster = PatternFamilyClusterer(bucket_ms=30_000, cooldown_buckets=4)
        first = cluster.observe({
            "asset": "BTC", "market": "perp", "start_ms": 0,
            "pattern_family": "cross_venue", "pattern_label": "cross_venue_consensus_buy",
        })
        adjacent = cluster.observe({
            "asset": "BTC", "market": "perp", "start_ms": 30_000,
            "pattern_family": "cross_venue", "pattern_label": "cross_venue_consensus_buy",
        })
        far = cluster.observe({
            "asset": "BTC", "market": "perp", "start_ms": 180_000,
            "pattern_family": "cross_venue", "pattern_label": "cross_venue_consensus_buy",
        })
        self.assertTrue(first["is_independent_event"])
        self.assertFalse(adjacent["is_independent_event"])
        self.assertTrue(far["is_independent_event"])


if __name__ == "__main__":
    unittest.main()

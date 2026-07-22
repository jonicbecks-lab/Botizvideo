import unittest
from types import SimpleNamespace

from live.hyperliquid_compat import CompatibleGalkaLiveEngine, CompatibleHyperliquidGateway
from live.engine import GalkaLiveEngine
from live.hyperliquid_gateway import HyperliquidGateway, PlacedOrder


class CompatibilityTests(unittest.TestCase):
    def test_old_class_names_keep_strict_main_implementations(self):
        self.assertTrue(issubclass(CompatibleGalkaLiveEngine, GalkaLiveEngine))
        self.assertTrue(issubclass(CompatibleHyperliquidGateway, HyperliquidGateway))

    def test_pending_target_status_is_accepted_without_inventing_oid(self):
        gateway = CompatibleHyperliquidGateway.__new__(CompatibleHyperliquidGateway)
        level = SimpleNamespace(index=1, price=100.0, size=0.1)
        response = {
            "status": "ok",
            "response": {
                "type": "order",
                "data": {"statuses": [{"resting": {"oid": 123}}, "waitingForTrigger"]},
            },
        }
        orders = gateway._parse_order_response(response, [level, None], ["entry", "target"])
        self.assertEqual(orders[0], PlacedOrder(123, "resting", 1, 100.0, 0.1, "entry"))
        self.assertEqual(orders[1].oid, 0)
        self.assertEqual(orders[1].status, "waitingForTrigger")
        self.assertEqual(orders[1].cloid, "target")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import os
import socket
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from live.agent_api import (
    AgentObservationService,
    AgentReadOnlyAPIServer,
    _allowed_client,
    load_or_create_agent_token,
)


class _Gateway:
    @staticmethod
    def account_state():
        return {
            "accountValue": 288.40,
            "withdrawable": 288.40,
            "totalMarginUsed": 0.0,
            "totalNotionalPosition": 0.0,
            "accountMode": "default",
            "positions": {},
        }

    @staticmethod
    def mids():
        return {"BTC": 79_000.0, "ETH": 2_400.0}

    @staticmethod
    def candles(coin, interval, limit):
        return [
            {
                "time": 1_787_330_000,
                "openTime": 1_787_330_000_000,
                "closeTime": 1_787_330_299_999,
                "open": 79_000.0,
                "high": 79_100.0,
                "low": 78_900.0,
                "close": 79_050.0,
                "volume": 12.0,
            }
        ]

    @staticmethod
    def candles_range(coin, interval, start_ms, end_ms):
        return [
            {
                "time": start_ms // 1000,
                "openTime": start_ms,
                "closeTime": min(end_ms, start_ms + 299_999),
                "open": 78_900.0,
                "high": 79_100.0,
                "low": 78_800.0,
                "close": 79_000.0,
                "volume": 10.0,
            }
        ]


class _Recorder:
    def __init__(self, root: Path):
        self.enabled = True
        self.settings = SimpleNamespace(root=root)

    @staticmethod
    def status():
        return {"enabled": True, "activeSessions": []}


class _Engine:
    def __init__(self, root: Path, port: int = 8098):
        self.config = SimpleNamespace(data_dir=root, port=port)
        self.lock = threading.RLock()
        self.gateway = _Gateway()
        self.research_recorder = _Recorder(root / "research" / "galka_campaigns")
        self.cluster_volume = None
        self.state = {
            "system": {
                "safeMode": False,
                "safeModeReason": None,
                "monitorHeartbeatAt": "2026-08-26T10:00:00Z",
                "lastReconcileAt": "2026-08-26T10:00:00Z",
                "lastGlobalCheckAt": "2026-08-26T10:00:00Z",
            },
            "events": [{"time": "2026-08-26T10:00:00Z", "type": "live", "message": "ok"}],
            "campaigns": {
                "BTC": {
                    "id": "HL-BTC-1787332928152-58df2a",
                    "coin": "BTC",
                    "status": "waiting",
                    "createdAt": "2026-08-21T17:22:08Z",
                    "createdMs": 1_787_332_928_152,
                    "galkaPrice": 76_814.84,
                    "actualNotional": 2_880.0,
                    "requiredMargin": 288.0,
                    "levels": [
                        {
                            "index": 1,
                            "depth_pct": 0.15,
                            "price": 76_699.0,
                            "size": 0.01,
                            "notional": 766.99,
                            "filledSize": 0.0,
                            "status": "resting",
                            "oid": 123456,
                            "entryCloid": "should-not-leak-in-summary",
                        }
                    ],
                    "researchSetup": {
                        "selectionMethod": "manual_crosshair_structure_v3",
                        "timeframe": "5m",
                        "anchorTimeMs": 1_787_329_200_000,
                        "anchorPrice": 76_814.84,
                        "leftBoundaryTimeMs": 1_787_328_600_000,
                        "leftBoundaryPrice": 77_000.0,
                        "rightBoundaryTimeMs": 1_787_330_000_000,
                        "rightBoundaryPrice": 77_100.0,
                        "derived": {"durationMs": 1_400_000},
                    },
                }
            },
        }


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class AgentAPIServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.engine = _Engine(self.root)
        campaign_dir = self.root / "research" / "campaigns"
        campaign_dir.mkdir(parents=True)
        campaign = {
            "schemaVersion": 1,
            "campaignId": "HL-BTC-1787332928152-58df2a",
            "coin": "BTC",
            "status": "completed",
            "createdAt": "2026-08-21T17:22:08Z",
            "createdMs": 1_787_332_928_152,
            "completedAt": "2026-08-21T18:22:08Z",
            "galkaPrice": 76_814.84,
            "actualNotional": 2_880.0,
            "grossPnl": 3.0,
            "fees": 0.4,
            "netPnlApprox": 2.6,
            "levels": [{"index": 1, "depthPct": 0.15, "price": 76_699.0, "filledSize": 0.01}],
            "researchSetup": {
                "selectionMethod": "manual_crosshair_structure_v3",
                "timeframe": "5m",
                "anchorTimeMs": 1_787_329_200_000,
                "anchorPrice": 76_814.84,
                "leftBoundaryTimeMs": 1_787_328_600_000,
                "leftBoundaryPrice": 77_000.0,
                "rightBoundaryTimeMs": 1_787_330_000_000,
                "rightBoundaryPrice": 77_100.0,
                "derived": {"durationMs": 1_400_000},
            },
        }
        (campaign_dir / "HL-BTC-1787332928152-58df2a.json").write_text(json.dumps(campaign), encoding="utf-8")

        recorder_dir = self.engine.research_recorder.settings.root / "BTC" / "HL-BTC-1787332928152-58df2a"
        recorder_dir.mkdir(parents=True)
        metadata = {
            "campaignId": "HL-BTC-1787332928152-58df2a",
            "executions": [
                {"ownerKind": "entry", "exchangeTimestampMs": 1_787_333_100_000, "price": 76_699.0}
            ],
            "returnToGalka": {"exchangeTimestampMs": 1_787_335_000_000},
            "campaignCompletedAtMs": 1_787_336_000_000,
        }
        (recorder_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
        (recorder_dir / "features.jsonl").write_text(
            json.dumps({"campaignId": "HL-BTC-1787332928152-58df2a", "cvd": 1.25}) + "\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_snapshot_is_compact_and_does_not_expose_order_ids(self):
        service = AgentObservationService(self.engine)
        result = service.snapshot("BTC")
        self.assertEqual(result["account"]["accountValue"], 288.40)
        campaign = result["selectedCampaign"]
        self.assertEqual(campaign["research"]["selectionMethod"], "manual_crosshair_structure_v3")
        self.assertNotIn("oid", campaign["levels"][0])
        self.assertNotIn("entryCloid", campaign["levels"][0])

    def test_history_context_and_research_expose_detective_inputs(self):
        service = AgentObservationService(self.engine)
        history = service.history("BTC", 10)
        self.assertEqual(len(history["campaigns"]), 1)
        campaign_id = history["campaigns"][0]["campaignId"]
        context = service.context(campaign_id, "5m", 30, 30)
        self.assertEqual(context["timeline"]["firstFillMs"], 1_787_333_100_000)
        self.assertEqual(context["timeline"]["returnToGalkaMs"], 1_787_335_000_000)
        self.assertEqual([row["id"] for row in context["phases"]], ["formation", "approach", "activeTrade", "postExit"])
        self.assertTrue(context["candles"])
        research = service.research(campaign_id, 20)
        self.assertEqual(research["recentFeatures"][0]["cvd"], 1.25)

    def test_token_file_is_stable_private_and_source_networks_are_restricted(self):
        token_path = self.root / "runtime" / "agent-readonly.token"
        first = load_or_create_agent_token(token_path)
        second = load_or_create_agent_token(token_path)
        self.assertEqual(first, second)
        self.assertEqual(token_path.stat().st_mode & 0o777, 0o600)
        self.assertTrue(_allowed_client("127.0.0.1"))
        self.assertTrue(_allowed_client("100.100.10.20"))
        self.assertFalse(_allowed_client("192.168.1.20"))
        self.assertFalse(_allowed_client("8.8.8.8"))


class AgentAPINetworkContractTests(unittest.TestCase):
    def test_bearer_get_works_and_post_is_method_not_allowed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            port = _free_port()
            engine = _Engine(root, port=port - 1)
            with patch.dict(os.environ, {"GALKA_AGENT_API_BIND": "127.0.0.1", "GALKA_AGENT_API_PORT": str(port)}, clear=False):
                server = AgentReadOnlyAPIServer(engine)
                server.start()
                try:
                    base = f"http://127.0.0.1:{port}/api/agent/v1"
                    request = urllib.request.Request(
                        base + "/schema",
                        headers={"Authorization": f"Bearer {server.token}"},
                    )
                    with urllib.request.urlopen(request, timeout=3) as response:
                        payload = json.loads(response.read().decode("utf-8"))
                    self.assertTrue(payload["ok"])
                    self.assertEqual(payload["data"]["mode"], "READ_ONLY")

                    bad = urllib.request.Request(base + "/schema", headers={"Authorization": "Bearer wrong"})
                    with self.assertRaises(urllib.error.HTTPError) as wrong:
                        urllib.request.urlopen(bad, timeout=3)
                    self.assertEqual(wrong.exception.code, 401)

                    post = urllib.request.Request(
                        base + "/schema",
                        data=b"{}",
                        method="POST",
                        headers={"Authorization": f"Bearer {server.token}"},
                    )
                    with self.assertRaises(urllib.error.HTTPError) as blocked:
                        urllib.request.urlopen(post, timeout=3)
                    self.assertEqual(blocked.exception.code, 405)
                finally:
                    server.stop()


if __name__ == "__main__":
    unittest.main()

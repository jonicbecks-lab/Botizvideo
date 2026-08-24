from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from live.control_center import ProjectControlCenter


class _AliveThread:
    ident = 123

    @staticmethod
    def is_alive() -> bool:
        return True


class _Gateway:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def mids(self):
        self.calls.append("mids")
        return {"BTC": 70000.0, "ETH": 3000.0, "SOL": 150.0, "BNB": 800.0}

    def candles(self, coin, interval, limit):
        self.calls.append(f"candles:{coin}:{interval}:{limit}")
        return [
            {"time": 1_700_000_000 + index * 300, "open": 100, "high": 101, "low": 99, "close": 100}
            for index in range(limit)
        ]

    def account_state(self):
        self.calls.append("account_state")
        return {
            "accountValue": 250.0,
            "withdrawable": 250.0,
            "positions": {},
            "balanceSource": "perp_margin_summary",
        }

    def fresh_account_state(self):
        self.calls.append("fresh_account_state")
        return self.account_state()

    def fresh_open_orders(self):
        self.calls.append("fresh_open_orders")
        return []


class _Recorder:
    @staticmethod
    def status():
        return {"enabled": True, "activeSessions": []}


class _Engine:
    def __init__(self, root: Path) -> None:
        self.lock = threading.RLock()
        self.action_lock = threading.RLock()
        self._manual_action_pending = threading.Event()
        self.monitor_thread = _AliveThread()
        self._auto_queue_thread = _AliveThread()
        self.gateway = _Gateway()
        self.research_recorder = _Recorder()
        research_root = root / "research"
        research_root.mkdir(parents=True)
        manifest = research_root / "manifest.json"
        manifest.write_text("{}", encoding="utf-8")
        self.research_journal = SimpleNamespace(
            root=research_root,
            manifest_path=manifest,
            _sync_thread=_AliveThread(),
        )
        self.cluster_volume = None
        self.config = SimpleNamespace(
            data_dir=root,
            network_name="mainnet",
            live_enabled=True,
            masked_address="0x1234…abcd",
            leverage=10,
            isolated=True,
            max_margin_fraction=0.99,
        )
        self.state = {
            "system": {"safeMode": False, "safeModeReason": None},
            "campaigns": {},
            "queuedGalkas": {},
        }

    @staticmethod
    def _size_tolerance(_coin: str) -> float:
        return 0.000001

    @staticmethod
    def _foreign_open_orders(_campaign, _orders):
        return []


class _DeterministicControlCenter(ProjectControlCenter):
    def _updater_snapshot(self):
        return {"worktreeClean": True, "error": None}

    def _research_snapshot(self):
        return {
            "recorder": {"enabled": True, "activeSessions": []},
            "journalRoot": str(self.engine.research_journal.root),
            "journalWritable": True,
            "syncThreadAlive": True,
            "latestLocalManifestMs": 1,
            "cluster": {"enabled": True, "connected": True},
        }

    def _logic_audit(self):
        return []


class ProjectControlCenterTests(unittest.TestCase):
    def test_check_now_is_read_only_and_reports_real_chain(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = _Engine(Path(directory))
            center = _DeterministicControlCenter(engine)
            before = repr(engine.state)

            result = center.check_now("BTC")

            self.assertEqual(result["overall"], "working")
            self.assertEqual(before, repr(engine.state))
            ids = {row["id"] for row in result["steps"]}
            self.assertTrue({"monitor", "mids", "candles", "account", "orders", "consistency", "journal", "clusters", "updater"}.issubset(ids))
            self.assertIn("fresh_account_state", engine.gateway.calls)
            self.assertIn("fresh_open_orders", engine.gateway.calls)
            self.assertFalse(engine._manual_action_pending.is_set())
            self.assertIn("read-only", result["note"])

    def test_check_does_not_fake_exchange_reads_when_action_lock_is_busy(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = _Engine(Path(directory))
            acquired = threading.Event()
            release = threading.Event()

            def hold_lock():
                with engine.action_lock:
                    acquired.set()
                    release.wait(2)

            thread = threading.Thread(target=hold_lock)
            thread.start()
            acquired.wait(1)
            try:
                result = _DeterministicControlCenter(engine).check_now("BTC")
            finally:
                release.set()
                thread.join(2)

            by_id = {row["id"]: row for row in result["steps"]}
            self.assertEqual(by_id["account"]["status"], "manual")
            self.assertEqual(by_id["orders"]["status"], "manual")
            self.assertEqual(by_id["consistency"]["status"], "manual")
            self.assertNotIn("fresh_account_state", engine.gateway.calls)
            self.assertNotIn("fresh_open_orders", engine.gateway.calls)

    def test_repository_audit_detects_paper_live_contract_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            center = ProjectControlCenter(_Engine(Path(directory)))
            audit = center._logic_audit()
            ids = {row["id"] for row in audit}
            self.assertIn("paper-vs-live", ids)
            self.assertIn("l1-rearm", ids)
            self.assertIn("notional-limit", ids)
            self.assertIn("research-return-semantics", ids)


if __name__ == "__main__":
    unittest.main()

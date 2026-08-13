from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from copy import deepcopy
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1


class ResearchJournal:
    """Best-effort local research log with optional GitHub synchronization.

    Trading must never depend on this logger. Every public method swallows I/O
    failures and records only non-secret campaign/market data.
    """

    def __init__(self, data_dir: Path, repo_root: Path):
        self.root = Path(data_dir) / "research"
        self.repo_root = Path(repo_root)
        self.campaign_dir = self.root / "campaigns"
        self.events_path = self.root / "events.jsonl"
        self.fills_path = self.root / "fills.jsonl"
        self.manifest_path = self.root / "manifest.json"
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._sync_thread: threading.Thread | None = None
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            self.campaign_dir.mkdir(parents=True, exist_ok=True)
            os.chmod(self.root, 0o700)
            os.chmod(self.campaign_dir, 0o700)
            self._write_manifest()
        except Exception:
            pass

    def start_background_sync(self) -> None:
        if self._sync_thread and self._sync_thread.is_alive():
            return
        self._sync_thread = threading.Thread(
            target=self._sync_loop,
            name="galka-research-github-sync",
            daemon=True,
        )
        self._sync_thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _write_manifest(self) -> None:
        payload = {
            "schemaVersion": SCHEMA_VERSION,
            "description": "Galka LIVE research dataset. No wallet keys or session secrets.",
            "updatedAt": self._now_iso(),
            "files": {
                "events": "events.jsonl",
                "fills": "fills.jsonl",
                "campaigns": "campaigns/<campaignId>.json",
            },
            "researchPurpose": {
                "strategyQuality": True,
                "ladderShapeAnalysis": True,
                "partialFillAnalysis": True,
                "postExitExtensionAnalysis": True,
                "counterfactualExitResearch": True,
            },
        }
        self._atomic_json(self.manifest_path, payload)

    @staticmethod
    def _now_iso() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    @staticmethod
    def _json_safe(value: Any) -> Any:
        try:
            json.dumps(value, allow_nan=False)
            return value
        except Exception:
            if isinstance(value, dict):
                return {str(k): ResearchJournal._json_safe(v) for k, v in value.items()}
            if isinstance(value, (list, tuple)):
                return [ResearchJournal._json_safe(v) for v in value]
            return str(value)

    @staticmethod
    def _historically_had_position(campaign: dict[str, Any]) -> bool:
        if bool(campaign.get("hadPosition")):
            return True
        if int(campaign.get("cycleDeepest") or 0) > 0:
            return True
        return any(float(level.get("filledSize") or 0) > 0 for level in campaign.get("levels", []))

    def _append_jsonl(self, path: Path, payload: dict[str, Any]) -> None:
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(self._json_safe(payload), ensure_ascii=False, allow_nan=False))
                handle.write("\n")
                handle.flush()
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass

    def _atomic_json(self, path: Path, payload: dict[str, Any]) -> None:
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(
                json.dumps(self._json_safe(payload), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                encoding="utf-8",
            )
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            os.replace(tmp, path)

    def append_event(
        self,
        event_type: str,
        message: str,
        meta: dict[str, Any],
        campaign: dict[str, Any] | None = None,
    ) -> None:
        try:
            row = {
                "schemaVersion": SCHEMA_VERSION,
                "recordedAt": self._now_iso(),
                "type": event_type,
                "message": message,
                "campaignId": meta.get("campaignId"),
                "coin": campaign.get("coin") if campaign else meta.get("coin"),
                "meta": deepcopy(meta),
            }
            self._append_jsonl(self.events_path, row)
            if campaign:
                self.upsert_campaign(campaign, reason=f"event:{event_type}")
        except Exception:
            pass

    def append_fill(self, campaign: dict[str, Any], fill: dict[str, Any]) -> None:
        try:
            row = {
                "schemaVersion": SCHEMA_VERSION,
                "recordedAt": self._now_iso(),
                "campaignId": campaign.get("id"),
                "coin": campaign.get("coin"),
                "galkaPrice": campaign.get("galkaPrice"),
                "ownerKind": fill.get("_ownerKind"),
                "ownerLevel": fill.get("_ownerLevel"),
                "oid": fill.get("oid"),
                "cloid": fill.get("cloid") or fill.get("_resolvedCloid"),
                "side": fill.get("side"),
                "direction": fill.get("direction"),
                "price": fill.get("price"),
                "size": fill.get("size"),
                "fee": fill.get("fee"),
                "closedPnl": fill.get("closedPnl"),
                "exchangeTimeMs": fill.get("time"),
                "hash": fill.get("hash"),
            }
            self._append_jsonl(self.fills_path, row)
        except Exception:
            pass

    def upsert_campaign(self, campaign: dict[str, Any], reason: str) -> None:
        try:
            campaign_id = str(campaign.get("id") or "")
            if not campaign_id:
                return
            levels = []
            for level in campaign.get("levels", []):
                levels.append(
                    {
                        "index": level.get("index"),
                        "depthPct": level.get("depth_pct"),
                        "price": level.get("price"),
                        "requestedSize": level.get("size"),
                        "notional": level.get("notional"),
                        "filledSize": level.get("filledSize"),
                        "averageFillPrice": level.get("averageFillPrice"),
                        "status": level.get("status"),
                    }
                )
            gross = float(campaign.get("cycleClosedPnl") or 0) + float(
                campaign.get("l1RealizedPnl") or 0
            )
            fees = float(campaign.get("cycleFees") or 0)
            setup_mid = campaign.get("setupMidPrice") or campaign.get("currentPrice")
            galka = float(campaign.get("galkaPrice") or 0)
            setup_distance_pct = None
            if setup_mid and float(setup_mid) > 0 and galka > 0:
                setup_distance_pct = (float(setup_mid) / galka - 1.0) * 100.0
            payload = {
                "schemaVersion": SCHEMA_VERSION,
                "updatedAt": self._now_iso(),
                "reason": reason,
                "campaignId": campaign_id,
                "coin": campaign.get("coin"),
                "status": campaign.get("status"),
                "createdAt": campaign.get("createdAt"),
                "createdMs": campaign.get("createdMs"),
                "completedAt": campaign.get("completedAt"),
                "galkaPrice": campaign.get("galkaPrice"),
                "setupMidPrice": setup_mid,
                "setupDistancePct": setup_distance_pct,
                "leverage": campaign.get("leverage"),
                "isolated": campaign.get("isolated"),
                "requestedNotional": campaign.get("requestedNotional"),
                "actualNotional": campaign.get("actualNotional"),
                "weightedAverage": campaign.get("weightedAverage"),
                "estimatedPnlAtGalka": campaign.get("estimatedPnlAtGalka"),
                "makerFeeRate": campaign.get("makerFeeRate"),
                "takerFeeRate": campaign.get("takerFeeRate"),
                "hadPosition": self._historically_had_position(campaign),
                "cycleDeepest": campaign.get("cycleDeepest"),
                "l1Cycles": campaign.get("l1Cycles"),
                "l1RealizedPnl": campaign.get("l1RealizedPnl"),
                "cycleClosedPnl": campaign.get("cycleClosedPnl"),
                "fees": fees,
                "grossPnl": gross,
                "netPnlApprox": gross - fees,
                "actualPositionSize": campaign.get("actualPositionSize"),
                "managedNetSize": campaign.get("managedNetSize"),
                "recoveryReason": campaign.get("recoveryReason"),
                "lastError": campaign.get("lastError"),
                "levels": levels,
                "postExitResearch": {
                    "status": "pending" if campaign.get("completedAt") else "not_ready",
                    "purpose": "Measure how far price continued above GALKA after exit for alternative exit/trailing-stop research.",
                    "horizonsMinutes": [1, 5, 15, 30, 60, 240, 1440],
                },
            }
            self._atomic_json(self.campaign_dir / f"{campaign_id}.json", payload)
            self._write_manifest()
        except Exception:
            pass

    def _sync_loop(self) -> None:
        script = self.repo_root / "scripts" / "galka-research-sync.sh"
        while not self._stop.wait(300):
            if not script.exists():
                continue
            try:
                subprocess.run(
                    ["bash", str(script), "--once"],
                    cwd=self.repo_root,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=45,
                    check=False,
                )
            except Exception:
                pass

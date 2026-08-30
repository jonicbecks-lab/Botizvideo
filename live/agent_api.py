from __future__ import annotations

import hmac
import ipaddress
import json
import math
import os
import re
import secrets
import socket
import stat
import subprocess
import threading
import time
from collections import deque
from copy import deepcopy
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .engine import ACTIVE_STATUSES, LiveEngineError
from .hyperliquid_gateway import INTERVAL_MS, SUPPORTED_COINS


API_VERSION = 1
TOKEN_BYTES = 48
MAX_JSON_FILE_BYTES = 6_000_000
MAX_HISTORY_LIMIT = 100
MAX_FEATURE_LIMIT = 500
MAX_EVENT_LIMIT = 500
MAX_CANDLE_LIMIT = 1500
DEFAULT_CONTEXT_BEFORE_MINUTES = 180
DEFAULT_CONTEXT_AFTER_MINUTES = 240
_REQUESTS_PER_MINUTE = 120
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,180}$")
_TAILSCALE_V4 = ipaddress.ip_network("100.64.0.0/10")
_TAILSCALE_V6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")


class AgentAPIError(RuntimeError):
    pass


def _now_ms() -> int:
    return int(time.time() * 1000)


def _iso_ms(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value) if math.isfinite(float(value)) else 0
    text = str(value).strip()
    if not text:
        return 0
    try:
        return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp() * 1000)
    except Exception:
        return 0


def _finite(value: Any, default: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _safe_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def _private_regular_file(path: Path) -> None:
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise AgentAPIError(f"Unsafe agent API token file: {path}")
    os.chmod(path, 0o600)


def load_or_create_agent_token(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    if path.exists() or path.is_symlink():
        _private_regular_file(path)
        token = path.read_text(encoding="utf-8").strip()
        if len(token) < 32:
            raise AgentAPIError("Agent API token is damaged")
        return token

    token = secrets.token_urlsafe(TOKEN_BYTES)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        os.write(descriptor, (token + "\n").encode("ascii"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _private_regular_file(path)
    return token


def _allowed_client(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if ip.is_loopback:
        return True
    if isinstance(ip, ipaddress.IPv4Address):
        return ip in _TAILSCALE_V4
    return ip in _TAILSCALE_V6


def discover_tailscale_ips() -> list[str]:
    values: set[str] = set()
    commands = (["ip", "-o", "-4", "addr", "show"], ["/system/bin/ip", "-o", "-4", "addr", "show"])
    for command in commands:
        try:
            result = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=2,
                check=False,
            )
        except Exception:
            continue
        for token in (result.stdout or "").split():
            candidate = token.split("/", 1)[0]
            try:
                ip = ipaddress.ip_address(candidate)
            except ValueError:
                continue
            if isinstance(ip, ipaddress.IPv4Address) and ip in _TAILSCALE_V4:
                values.add(str(ip))
        if values:
            break

    if not values:
        try:
            for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                candidate = item[4][0]
                ip = ipaddress.ip_address(candidate)
                if ip in _TAILSCALE_V4:
                    values.add(str(ip))
        except Exception:
            pass
    return sorted(values)


def _read_json(path: Path, *, maximum_bytes: int = MAX_JSON_FILE_BYTES) -> dict[str, Any] | None:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > maximum_bytes:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except Exception:
        return None


def _tail_jsonl(path: Path, limit: int, *, maximum_scan_lines: int = 20_000) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    rows: deque[dict[str, Any]] = deque(maxlen=limit)
    try:
        if path.is_symlink() or not path.is_file():
            return []
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            tail: deque[str] = deque(maxlen=max(limit * 8, min(maximum_scan_lines, 2000)))
            for line in handle:
                tail.append(line)
        for line in tail:
            try:
                value = json.loads(line)
            except Exception:
                continue
            if isinstance(value, dict):
                rows.append(value)
    except Exception:
        return []
    return list(rows)


def _git_sha(repo_root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2,
            check=False,
        )
        value = (result.stdout or "").strip()
        return value if len(value) == 40 else None
    except Exception:
        return None


class AgentObservationService:
    """Read-only projection of GALKA LIVE for an external analysis agent.

    It intentionally exposes no exchange mutation primitive, private wallet key,
    browser session token, order cancel endpoint, updater action or SAFE MODE reset.
    """

    def __init__(self, engine: Any):
        self.engine = engine
        self.repo_root = Path(__file__).resolve().parents[1]
        self.data_dir = Path(engine.config.data_dir)
        self.research_root = self.data_dir / "research"
        self.campaign_dir = self.research_root / "campaigns"
        self.events_path = self.research_root / "events.jsonl"
        self.fills_path = self.research_root / "fills.jsonl"
        self.version_sha = _git_sha(self.repo_root)

    @staticmethod
    def _coin(value: str) -> str:
        coin = str(value or "").upper().replace("USDT", "").replace("USD", "")
        if coin not in SUPPORTED_COINS:
            raise AgentAPIError(f"Unsupported coin: {value}")
        return coin

    @staticmethod
    def _campaign_id(value: str) -> str:
        campaign_id = str(value or "").strip()
        if not campaign_id or not _SAFE_ID.fullmatch(campaign_id):
            raise AgentAPIError("Invalid campaign id")
        return campaign_id

    @staticmethod
    def _level(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "index": row.get("index"),
            "depthPct": row.get("depthPct", row.get("depth_pct")),
            "price": row.get("price"),
            "notional": row.get("notional"),
            "requestedSize": row.get("requestedSize", row.get("size")),
            "filledSize": row.get("filledSize"),
            "averageFillPrice": row.get("averageFillPrice"),
            "status": row.get("status"),
        }

    def _compact_campaign(self, row: dict[str, Any]) -> dict[str, Any]:
        research_setup = row.get("researchSetup") if isinstance(row.get("researchSetup"), dict) else None
        derived = research_setup.get("derived") if isinstance(research_setup, dict) and isinstance(research_setup.get("derived"), dict) else None
        levels = [self._level(level) for level in row.get("levels", []) if isinstance(level, dict)]
        return {
            "campaignId": row.get("campaignId") or row.get("id"),
            "coin": row.get("coin"),
            "status": row.get("status"),
            "createdAt": row.get("createdAt"),
            "createdMs": row.get("createdMs"),
            "completedAt": row.get("completedAt"),
            "galkaPrice": row.get("galkaPrice"),
            "setupMidPrice": row.get("setupMidPrice") or row.get("currentPrice"),
            "setupDistancePct": row.get("setupDistancePct"),
            "actualNotional": row.get("actualNotional"),
            "requestedNotional": row.get("requestedNotional"),
            "requiredMargin": row.get("requiredMargin"),
            "weightedAverage": row.get("weightedAverage"),
            "hadPosition": row.get("hadPosition"),
            "cycleDeepest": row.get("cycleDeepest"),
            "l1Cycles": row.get("l1Cycles"),
            "actualPositionSize": row.get("actualPositionSize"),
            "managedNetSize": row.get("managedNetSize"),
            "grossPnl": row.get("grossPnl"),
            "fees": row.get("fees", row.get("cycleFees")),
            "netPnlApprox": row.get("netPnlApprox", row.get("finalClosedPnl")),
            "recoveryReason": row.get("recoveryReason"),
            "lastError": row.get("lastError"),
            "research": {
                "selectionMethod": research_setup.get("selectionMethod") if research_setup else None,
                "timeframe": research_setup.get("timeframe") if research_setup else None,
                "anchorTimeMs": research_setup.get("anchorTimeMs") if research_setup else None,
                "leftBoundaryTimeMs": research_setup.get("leftBoundaryTimeMs") if research_setup else None,
                "rightBoundaryTimeMs": research_setup.get("rightBoundaryTimeMs") if research_setup else None,
                "leftBoundaryPrice": research_setup.get("leftBoundaryPrice") if research_setup else None,
                "rightBoundaryPrice": research_setup.get("rightBoundaryPrice") if research_setup else None,
                "derived": deepcopy(derived),
            },
            "levels": levels,
        }

    def _runtime_campaigns(self) -> list[dict[str, Any]]:
        with self.engine.lock:
            rows = [
                deepcopy(row)
                for row in (self.engine.state.get("campaigns") or {}).values()
                if isinstance(row, dict)
            ]
        return rows

    def _campaign_payload(self, campaign_id: str) -> dict[str, Any]:
        campaign_id = self._campaign_id(campaign_id)
        journal_path = self.campaign_dir / f"{campaign_id}.json"
        payload = _read_json(journal_path)
        if payload is not None:
            return payload
        for row in self._runtime_campaigns():
            if str(row.get("id") or "") == campaign_id:
                result = deepcopy(row)
                result["campaignId"] = campaign_id
                return result
        raise AgentAPIError(f"Campaign not found: {campaign_id}")

    def _recorder_metadata(self, campaign_id: str, coin: str) -> dict[str, Any] | None:
        recorder = getattr(self.engine, "research_recorder", None)
        settings = getattr(recorder, "settings", None)
        root = Path(getattr(settings, "root", self.research_root / "galka_campaigns"))
        path = root / self._coin(coin) / self._campaign_id(campaign_id) / "metadata.json"
        return _read_json(path)

    def _fill_rows(self, campaign_id: str, limit: int = 500) -> list[dict[str, Any]]:
        campaign_id = self._campaign_id(campaign_id)
        rows = _tail_jsonl(self.fills_path, max(limit * 5, 1000), maximum_scan_lines=50_000)
        result = [row for row in rows if str(row.get("campaignId") or "") == campaign_id]
        return result[-limit:]

    def schema(self) -> dict[str, Any]:
        return {
            "apiVersion": API_VERSION,
            "mode": "READ_ONLY",
            "purpose": "Observation and research API for the GALKA Detective/OpenClaw agent.",
            "recommendedPollingSeconds": 10,
            "rules": [
                "This API cannot place, cancel or close orders and cannot clear SAFE MODE.",
                "researchSetup.manual_crosshair_structure_v3 is the human ground truth for GALKA geometry.",
                "Do not infer a missing value: null means the event/field was not recorded or cannot be verified.",
                "Before interpreting returnToGalka as return after a fill, compare it with firstFillMs.",
                "Trading changes must be proposed for human approval; research observations are not trading commands.",
            ],
            "phases": [
                {
                    "id": "formation",
                    "meaning": "What price did before the GALKA was confirmed, including the manually marked left/anchor/right structure.",
                },
                {
                    "id": "approach",
                    "meaning": "From GALKA confirmation until the first real entry fill; price is still approaching the ladder.",
                },
                {
                    "id": "activeTrade",
                    "meaning": "From first fill through deeper fills and the return/exit at GALKA.",
                },
                {
                    "id": "postExit",
                    "meaning": "Price behaviour after the campaign is finished; used for hold-longer/continuation research.",
                },
            ],
            "endpoints": {
                "snapshot": "GET /api/agent/v1/snapshot?coin=BTC",
                "history": "GET /api/agent/v1/history?coin=BTC&limit=50",
                "campaign": "GET /api/agent/v1/campaign?id=<campaignId>",
                "context": "GET /api/agent/v1/context?id=<campaignId>&interval=5m&beforeMin=180&afterMin=240",
                "research": "GET /api/agent/v1/research?id=<campaignId>&features=200",
                "candles": "GET /api/agent/v1/candles?coin=BTC&interval=5m&limit=300",
                "events": "GET /api/agent/v1/events?limit=100",
            },
        }

    def snapshot(self, coin: str) -> dict[str, Any]:
        normalized = self._coin(coin)
        with self.engine.lock:
            system = deepcopy(self.engine.state.get("system") or {})
            events = deepcopy((self.engine.state.get("events") or [])[-50:])
        account_error = None
        market_error = None
        try:
            account = self.engine.gateway.account_state()
        except Exception as exc:
            account = None
            account_error = f"{type(exc).__name__}: {exc}"
        try:
            mids = self.engine.gateway.mids()
        except Exception as exc:
            mids = {}
            market_error = f"{type(exc).__name__}: {exc}"

        campaigns = [self._compact_campaign(row) for row in self._runtime_campaigns()]
        active = [row for row in campaigns if str(row.get("status") or "") in ACTIVE_STATUSES]
        recorder = getattr(self.engine, "research_recorder", None)
        try:
            recorder_status = recorder.status() if recorder is not None else {"enabled": False}
        except Exception as exc:
            recorder_status = {"enabled": False, "error": str(exc)}
        cluster = getattr(self.engine, "cluster_volume", None)
        try:
            from .cluster_volume import ClusterVolumeService

            cluster_status = ClusterVolumeService.status(cluster) if cluster is not None else {"enabled": False}
        except Exception as exc:
            cluster_status = {"enabled": False, "error": str(exc)}

        account_summary = None
        if isinstance(account, dict):
            positions = {}
            for key, row in (account.get("positions") or {}).items():
                if not isinstance(row, dict):
                    continue
                positions[str(key)] = {
                    "size": row.get("size"),
                    "entryPrice": row.get("entryPrice"),
                    "liquidationPrice": row.get("liquidationPrice"),
                    "marginUsed": row.get("marginUsed"),
                    "positionValue": row.get("positionValue"),
                    "unrealizedPnl": row.get("unrealizedPnl"),
                }
            account_summary = {
                "accountValue": account.get("accountValue"),
                "withdrawable": account.get("withdrawable"),
                "totalMarginUsed": account.get("totalMarginUsed"),
                "totalNotionalPosition": account.get("totalNotionalPosition"),
                "accountMode": account.get("accountMode"),
                "positions": positions,
            }

        return {
            "apiVersion": API_VERSION,
            "observedAtMs": _now_ms(),
            "projectVersion": self.version_sha,
            "coin": normalized,
            "market": {"mid": mids.get(normalized), "mids": mids, "error": market_error},
            "account": account_summary,
            "accountError": account_error,
            "system": {
                "safeMode": bool(system.get("safeMode")),
                "safeModeReason": system.get("safeModeReason"),
                "monitorHeartbeatAt": system.get("monitorHeartbeatAt"),
                "lastReconcileAt": system.get("lastReconcileAt"),
                "lastGlobalCheckAt": system.get("lastGlobalCheckAt"),
            },
            "activeCampaigns": active,
            "selectedCampaign": next((row for row in active if row.get("coin") == normalized), None),
            "researchRecorder": recorder_status,
            "clusterStream": cluster_status,
            "recentEvents": events,
        }

    def history(self, coin: str | None, limit: int) -> dict[str, Any]:
        normalized = self._coin(coin) if coin else None
        limit = _safe_int(limit, 50, 1, MAX_HISTORY_LIMIT)
        files: list[Path] = []
        try:
            files = sorted(
                (path for path in self.campaign_dir.glob("*.json") if path.is_file() and not path.is_symlink()),
                key=lambda path: path.stat().st_mtime_ns,
                reverse=True,
            )
        except Exception:
            files = []

        output: list[dict[str, Any]] = []
        for path in files[: max(300, limit * 4)]:
            payload = _read_json(path)
            if not payload:
                continue
            if normalized and str(payload.get("coin") or "").upper() != normalized:
                continue
            output.append(self._compact_campaign(payload))
            if len(output) >= limit:
                break
        return {"apiVersion": API_VERSION, "coin": normalized, "campaigns": output}

    def campaign(self, campaign_id: str) -> dict[str, Any]:
        payload = self._campaign_payload(campaign_id)
        compact = self._compact_campaign(payload)
        coin = str(payload.get("coin") or "").upper()
        metadata = self._recorder_metadata(campaign_id, coin) if coin else None
        fills = self._fill_rows(campaign_id, 500)
        return {
            "apiVersion": API_VERSION,
            "campaign": payload,
            "summary": compact,
            "recorderMetadata": metadata,
            "fills": fills,
        }

    def candles(self, coin: str, interval: str, limit: int) -> dict[str, Any]:
        normalized = self._coin(coin)
        if interval not in INTERVAL_MS:
            raise AgentAPIError(f"Unsupported interval: {interval}")
        limit = _safe_int(limit, 300, 50, MAX_CANDLE_LIMIT)
        rows = self.engine.gateway.candles(normalized, interval, limit)
        return {"apiVersion": API_VERSION, "coin": normalized, "interval": interval, "candles": rows}

    def context(
        self,
        campaign_id: str,
        interval: str,
        before_minutes: int,
        after_minutes: int,
    ) -> dict[str, Any]:
        if interval not in INTERVAL_MS:
            raise AgentAPIError(f"Unsupported interval: {interval}")
        payload = self._campaign_payload(campaign_id)
        coin = self._coin(str(payload.get("coin") or ""))
        setup = payload.get("researchSetup") if isinstance(payload.get("researchSetup"), dict) else {}
        metadata = self._recorder_metadata(campaign_id, coin) or {}
        executions = metadata.get("executions") if isinstance(metadata.get("executions"), list) else []
        first_fill_ms = min(
            (
                int(row.get("exchangeTimestampMs") or 0)
                for row in executions
                if isinstance(row, dict)
                and str(row.get("ownerKind") or "").lower() == "entry"
                and int(row.get("exchangeTimestampMs") or 0) > 0
            ),
            default=0,
        )
        if not first_fill_ms:
            fills = self._fill_rows(campaign_id, 500)
            first_fill_ms = min(
                (
                    int(row.get("exchangeTimeMs") or 0)
                    for row in fills
                    if str(row.get("ownerKind") or "").lower() == "entry"
                    and int(row.get("exchangeTimeMs") or 0) > 0
                ),
                default=0,
            )

        left_ms = int(setup.get("leftBoundaryTimeMs") or 0)
        anchor_ms = int(setup.get("anchorTimeMs") or 0)
        right_ms = int(setup.get("rightBoundaryTimeMs") or 0)
        created_ms = int(payload.get("createdMs") or 0)
        return_meta = metadata.get("returnToGalka") if isinstance(metadata.get("returnToGalka"), dict) else {}
        return_ms = int(return_meta.get("exchangeTimestampMs") or 0)
        completed_ms = int(metadata.get("campaignCompletedAtMs") or 0) or _iso_ms(payload.get("completedAt"))
        now_ms = _now_ms()
        before_minutes = _safe_int(before_minutes, DEFAULT_CONTEXT_BEFORE_MINUTES, 0, 1440)
        after_minutes = _safe_int(after_minutes, DEFAULT_CONTEXT_AFTER_MINUTES, 0, 1440)

        structural_times = [value for value in (left_ms, anchor_ms, right_ms, created_ms) if value > 0]
        window_start = (min(structural_times) if structural_times else max(1, now_ms - 3_600_000)) - before_minutes * 60_000
        terminal_ms = completed_ms if completed_ms > 0 else now_ms
        window_end = min(now_ms, terminal_ms + after_minutes * 60_000)
        if window_end <= window_start:
            window_end = min(now_ms, window_start + max(INTERVAL_MS[interval] * 50, 60_000))

        span_bars = int(math.ceil((window_end - window_start) / INTERVAL_MS[interval])) + 4
        if span_bars > MAX_CANDLE_LIMIT:
            raise AgentAPIError(
                f"Context window is too large for {interval}: {span_bars} bars; reduce beforeMin/afterMin or use a larger interval"
            )

        gateway = self.engine.gateway
        if hasattr(gateway, "candles_range"):
            candle_rows = gateway.candles_range(coin, interval, max(1, window_start), window_end)
        else:
            candle_rows = gateway.candles(coin, interval, min(MAX_CANDLE_LIMIT, max(50, span_bars)))

        phases = [
            {
                "id": "formation",
                "fromMs": max(1, window_start),
                "toMs": created_ms or anchor_ms or None,
                "meaning": "Market structure before the GALKA was confirmed.",
            },
            {
                "id": "approach",
                "fromMs": created_ms or None,
                "toMs": first_fill_ms or None,
                "meaning": "GALKA is already placed; price approaches the ladder before the first entry fill.",
            },
            {
                "id": "activeTrade",
                "fromMs": first_fill_ms or None,
                "toMs": completed_ms or None,
                "returnToGalkaMs": return_ms or None,
                "meaning": "From first entry fill through the return/exit at GALKA.",
            },
            {
                "id": "postExit",
                "fromMs": completed_ms or None,
                "toMs": window_end if completed_ms else None,
                "meaning": "What price did after the standard GALKA campaign ended.",
            },
        ]
        return {
            "apiVersion": API_VERSION,
            "campaignId": campaign_id,
            "coin": coin,
            "interval": interval,
            "timeline": {
                "leftBoundaryMs": left_ms or None,
                "anchorMs": anchor_ms or None,
                "rightBoundaryMs": right_ms or None,
                "campaignCreatedMs": created_ms or None,
                "firstFillMs": first_fill_ms or None,
                "returnToGalkaMs": return_ms or None,
                "campaignCompletedMs": completed_ms or None,
            },
            "humanGeometry": {
                "galkaPrice": payload.get("galkaPrice"),
                "selectionMethod": setup.get("selectionMethod") if setup else None,
                "timeframe": setup.get("timeframe") if setup else None,
                "anchorPrice": setup.get("anchorPrice") if setup else None,
                "leftBoundaryPrice": setup.get("leftBoundaryPrice") if setup else None,
                "rightBoundaryPrice": setup.get("rightBoundaryPrice") if setup else None,
                "derived": deepcopy(setup.get("derived")) if isinstance(setup.get("derived"), dict) else None,
            },
            "phases": phases,
            "candles": candle_rows,
        }

    def research(self, campaign_id: str, feature_limit: int) -> dict[str, Any]:
        payload = self._campaign_payload(campaign_id)
        coin = self._coin(str(payload.get("coin") or ""))
        recorder = getattr(self.engine, "research_recorder", None)
        settings = getattr(recorder, "settings", None)
        root = Path(getattr(settings, "root", self.research_root / "galka_campaigns"))
        base = root / coin / self._campaign_id(campaign_id)
        feature_limit = _safe_int(feature_limit, 200, 0, MAX_FEATURE_LIMIT)
        metadata = _read_json(base / "metadata.json")
        features = _tail_jsonl(base / "features.jsonl", feature_limit) if feature_limit else []
        manifest = _read_json(base / "dataset_manifest.json")
        return {
            "apiVersion": API_VERSION,
            "campaignId": campaign_id,
            "coin": coin,
            "recorderEnabled": bool(getattr(recorder, "enabled", False)),
            "metadata": metadata,
            "datasetManifest": manifest,
            "recentFeatures": features,
            "rawTradesAvailable": (base / "trades.raw.jsonl").is_file(),
            "rawOrderbookAvailable": (base / "orderbook.raw.jsonl").is_file(),
            "note": "Raw trades/orderbook are intentionally not returned by HTTP v1; use derived features to keep observation lightweight.",
        }

    def events(self, limit: int) -> dict[str, Any]:
        limit = _safe_int(limit, 100, 1, MAX_EVENT_LIMIT)
        return {"apiVersion": API_VERSION, "events": _tail_jsonl(self.events_path, limit)}


class _RateLimiter:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._rows: dict[str, deque[float]] = {}

    def allow(self, client: str) -> bool:
        now = time.monotonic()
        cutoff = now - 60.0
        with self._lock:
            rows = self._rows.setdefault(client, deque())
            while rows and rows[0] < cutoff:
                rows.popleft()
            if len(rows) >= _REQUESTS_PER_MINUTE:
                return False
            rows.append(now)
            if len(self._rows) > 32:
                stale = [key for key, value in self._rows.items() if not value or value[-1] < cutoff]
                for key in stale:
                    self._rows.pop(key, None)
            return True


class AgentReadOnlyRequestHandler(BaseHTTPRequestHandler):
    server_version = "GalkaAgentReadOnly/1"
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    @property
    def _service(self) -> AgentObservationService:
        return self.server.observation_service  # type: ignore[attr-defined]

    @property
    def _token(self) -> str:
        return self.server.agent_token  # type: ignore[attr-defined]

    def _json(self, status: int, payload: dict[str, Any] | list[Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        client = str(self.client_address[0] if self.client_address else "")
        if not _allowed_client(client):
            return False
        limiter = self.server.rate_limiter  # type: ignore[attr-defined]
        if not limiter.allow(client):
            self._json(HTTPStatus.TOO_MANY_REQUESTS, {"ok": False, "error": "Agent API rate limit exceeded"})
            return False
        authorization = str(self.headers.get("Authorization") or "")
        supplied = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
        if not supplied:
            supplied = str(self.headers.get("X-Galka-Agent-Token") or "")
        return bool(supplied) and hmac.compare_digest(supplied, self._token)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/healthz":
            if not _allowed_client(str(self.client_address[0] if self.client_address else "")):
                self._json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "client network not allowed"})
                return
            self._json(HTTPStatus.OK, {"ok": True, "server": "galka-agent-readonly", "apiVersion": API_VERSION})
            return
        if not parsed.path.startswith("/api/agent/v1/"):
            self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})
            return
        if not self._authorized():
            if getattr(self, "_headers_buffer", None):
                return
            self._json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "invalid read-only agent token or client network"})
            return

        query = parse_qs(parsed.query)
        try:
            if parsed.path == "/api/agent/v1/schema":
                data = self._service.schema()
            elif parsed.path == "/api/agent/v1/snapshot":
                data = self._service.snapshot(query.get("coin", ["BTC"])[0])
            elif parsed.path == "/api/agent/v1/history":
                coin = query.get("coin", [""])[0] or None
                data = self._service.history(coin, query.get("limit", ["50"])[0])
            elif parsed.path == "/api/agent/v1/campaign":
                data = self._service.campaign(query.get("id", [""])[0])
            elif parsed.path == "/api/agent/v1/candles":
                data = self._service.candles(
                    query.get("coin", ["BTC"])[0],
                    query.get("interval", ["5m"])[0],
                    query.get("limit", ["300"])[0],
                )
            elif parsed.path == "/api/agent/v1/context":
                data = self._service.context(
                    query.get("id", [""])[0],
                    query.get("interval", ["5m"])[0],
                    query.get("beforeMin", [str(DEFAULT_CONTEXT_BEFORE_MINUTES)])[0],
                    query.get("afterMin", [str(DEFAULT_CONTEXT_AFTER_MINUTES)])[0],
                )
            elif parsed.path == "/api/agent/v1/research":
                data = self._service.research(
                    query.get("id", [""])[0],
                    query.get("features", ["200"])[0],
                )
            elif parsed.path == "/api/agent/v1/events":
                data = self._service.events(query.get("limit", ["100"])[0])
            else:
                self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})
                return
        except (AgentAPIError, LiveEngineError, ValueError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
            return
        except Exception as exc:
            self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"ok": False, "error": f"observation unavailable: {type(exc).__name__}: {exc}"})
            return
        self._json(HTTPStatus.OK, {"ok": True, "data": data})

    def do_POST(self) -> None:  # noqa: N802
        self._json(
            HTTPStatus.METHOD_NOT_ALLOWED,
            {"ok": False, "error": "READ ONLY API: POST is not supported"},
        )

    def do_PUT(self) -> None:  # noqa: N802
        self.do_POST()

    def do_DELETE(self) -> None:  # noqa: N802
        self.do_POST()

    def do_PATCH(self) -> None:  # noqa: N802
        self.do_POST()


class AgentReadOnlyAPIServer:
    """Separate read-only listener for OpenClaw/Detective observation.

    Binding is separate from the trading UI. Even if the agent token is leaked, it
    cannot authenticate to /api/live/* because that API uses a different browser
    session token on a different listener.
    """

    def __init__(self, engine: Any):
        self.engine = engine
        self.host = str(os.environ.get("GALKA_AGENT_API_BIND", "0.0.0.0")).strip() or "0.0.0.0"
        if self.host not in {"0.0.0.0", "127.0.0.1", "localhost"}:
            raise AgentAPIError("GALKA_AGENT_API_BIND must be 0.0.0.0, 127.0.0.1 or localhost")
        self.port = _safe_int(
            os.environ.get("GALKA_AGENT_API_PORT", int(engine.config.port) + 1),
            int(engine.config.port) + 1,
            1024,
            65535,
        )
        token_path = Path(
            os.environ.get(
                "GALKA_AGENT_API_TOKEN_FILE",
                Path(engine.config.data_dir) / "runtime" / "agent-readonly.token",
            )
        ).expanduser().absolute()
        repo_root = Path(__file__).resolve().parents[1]
        try:
            token_path.relative_to(repo_root)
        except ValueError:
            pass
        else:
            raise AgentAPIError("Agent API token file must stay outside the Git repository")
        self.token_path = token_path
        self.token = load_or_create_agent_token(token_path)
        self.service = AgentObservationService(engine)
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._error: str | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        server = ThreadingHTTPServer((self.host, self.port), AgentReadOnlyRequestHandler)
        server.daemon_threads = True
        server.observation_service = self.service  # type: ignore[attr-defined]
        server.agent_token = self.token  # type: ignore[attr-defined]
        server.rate_limiter = _RateLimiter()  # type: ignore[attr-defined]
        self._server = server
        self._thread = threading.Thread(
            target=server.serve_forever,
            kwargs={"poll_interval": 0.5},
            name="galka-agent-readonly-api",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        server = self._server
        if server is not None:
            try:
                server.shutdown()
                server.server_close()
            except Exception:
                pass
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=3.0)
        self._server = None
        self._thread = None

    def status(self, *, include_token: bool = False) -> dict[str, Any]:
        tailscale_ips = discover_tailscale_ips()
        running = bool(self._thread and self._thread.is_alive())
        local_base = f"http://127.0.0.1:{self.port}/api/agent/v1"
        tailscale_bases = [f"http://{ip}:{self.port}/api/agent/v1" for ip in tailscale_ips]
        result = {
            "enabled": True,
            "running": running,
            "readOnly": True,
            "bind": self.host,
            "port": self.port,
            "tokenPath": str(self.token_path),
            "localBaseUrl": local_base,
            "tailscaleBaseUrls": tailscale_bases,
            "preferredBaseUrl": tailscale_bases[0] if tailscale_bases else local_base,
            "allowedClients": ["loopback", "Tailscale IPv4 100.64.0.0/10", "Tailscale IPv6 fd7a:115c:a1e0::/48"],
            "authHeader": "Authorization: Bearer <token>",
            "apiVersion": API_VERSION,
            "error": self._error,
        }
        if include_token:
            result["token"] = self.token
        return result

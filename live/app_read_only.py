from __future__ import annotations

import math
import threading
import time
from collections import defaultdict, deque
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

SUPPORTED_COINS = ("BTC", "ETH", "SOL")
SUPPORTED_INTERVALS = ("1m", "5m", "15m", "1h", "4h", "1d")
MAX_CANDLE_LIMIT = 1000
MAX_EVENT_LIMIT = 200


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _public_order(order: dict[str, Any]) -> dict[str, Any]:
    return {
        key: deepcopy(order.get(key))
        for key in ("oid", "side", "price", "size", "originalSize", "reduceOnly", "orderType", "isTrigger", "triggerPrice", "timestamp")
        if order.get(key) is not None
    }


def _public_campaign(campaign: dict[str, Any] | None, levels: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not campaign:
        return None
    return {
        "id": campaign.get("id"),
        "coin": campaign.get("coin"),
        "status": campaign.get("status"),
        "galkaPrice": _number(campaign.get("galkaPrice")),
        "createdAt": campaign.get("createdAt"),
        "updatedAt": campaign.get("updatedAt"),
        "leverage": campaign.get("leverage"),
        "isolated": bool(campaign.get("isolated")),
        "levels": levels,
        "filledLevelCount": sum(level.get("status") in {"filled", "partial"} for level in levels),
        "l1Cycles": campaign.get("l1Cycles", 0),
        "recoveryReason": str(campaign.get("recoveryReason") or "")[:500] or None,
    }


def build_snapshot(status: dict[str, Any], open_orders: list[dict[str, Any]]) -> dict[str, Any]:
    """Project one Engine read into the public app contract; never calculate strategy state."""
    system = status.get("system") or {}
    account_state = status.get("accountState") or {}
    positions = account_state.get("positions") or {}
    campaigns = status.get("campaigns") or {}
    mids = status.get("mids") or {}
    safe_mode = bool(system.get("safeMode"))
    monitor_failed = bool(status.get("liveEnabled") and system.get("monitorStarted") and not system.get("monitorAlive"))
    mode = "SAFE_MODE" if safe_mode or monitor_failed else ("LIVE" if status.get("liveEnabled") else "OFFLINE")
    safe_reason = system.get("safeModeReason") or ("Monitor stopped" if monitor_failed else None)
    markets: dict[str, Any] = {}
    for coin in SUPPORTED_COINS:
        position = positions.get(coin) or None
        campaign = campaigns.get(coin) or None
        coin_orders = [_public_order(row) for row in open_orders if row.get("coin") == coin]
        entry_orders = [row for row in coin_orders if not row.get("reduceOnly")]
        reduce_orders = [row for row in coin_orders if row.get("reduceOnly")]
        size = _number(position.get("size")) if position else None
        pnl_usd = _number(position.get("unrealizedPnl")) if position else None
        margin = _number(position.get("marginUsed")) if position else None
        pnl_percent = (pnl_usd / margin * 100) if pnl_usd is not None and margin not in {None, 0.0} else None
        levels = []
        for level in (campaign or {}).get("levels", []):
            levels.append({
                "index": level.get("index"),
                "price": _number(level.get("price")),
                "filledSize": _number(level.get("filledSize")) or 0.0,
                "status": level.get("status"),
            })
        markets[coin] = {
            "currentPrice": _number(mids.get(coin)),
            "position": position is not None and bool(size),
            "direction": "LONG" if size and size > 0 else ("SHORT" if size and size < 0 else None),
            "size": size,
            "entryPrice": _number(position.get("entryPrice")) if position else None,
            "leverage": (campaign or {}).get("leverage") or status.get("leverage"),
            "margin": margin,
            "pnlUsd": pnl_usd,
            "pnlPercent": pnl_percent,
            "activeCampaign": _public_campaign(campaign, levels),
            "galkaPrice": _number((campaign or {}).get("galkaPrice")),
            "levels": levels,
            "tp": _number((campaign or {}).get("galkaPrice")) if reduce_orders else None,
            "averageEntry": _number(position.get("entryPrice")) if position else None,
            "activeEntryOrders": entry_orders,
            "activeReduceOnlyOrders": reduce_orders,
            "closeNearMarketOrder": next((row for row in reduce_orders if not row.get("isTrigger")), None),
            "protectionStatus": "PROTECTED" if position and reduce_orders else ("UNPROTECTED" if position else "NOT_REQUIRED"),
        }
    server_time = status.get("serverTime") or int(time.time() * 1000)
    return {
        "serverTime": server_time,
        "lastSuccessfulSync": system.get("lastReconcileAt") or system.get("lastGlobalCheckAt"),
        "mode": mode,
        "safeModeReason": safe_reason,
        "account": {
            "balance": _number(account_state.get("accountValue")),
            "availableMargin": _number(account_state.get("withdrawable")),
            "usedMargin": _number(account_state.get("totalMarginUsed")),
            "unrealizedPnl": sum((_number(row.get("unrealizedPnl")) or 0.0) for row in positions.values()),
        },
        "markets": markets,
        "systemStatuses": sanitize_events(status.get("events") or [])[-20:],
    }


def normalize_candles(rows: list[Any], *, from_ms: int | None = None, to_ms: int | None = None) -> list[dict[str, Any]]:
    clean: dict[int, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        timestamp = row.get("openTime") if row.get("openTime") is not None else (_number(row.get("time")) or 0) * 1000
        try:
            timestamp = int(timestamp)
        except (TypeError, ValueError):
            continue
        values = {key: _number(row.get(key)) for key in ("open", "high", "low", "close", "volume")}
        if timestamp <= 0 or any(values[key] is None for key in ("open", "high", "low", "close")):
            continue
        if values["high"] < max(values["open"], values["close"]) or values["low"] > min(values["open"], values["close"]):
            continue
        if from_ms is not None and timestamp < from_ms or to_ms is not None and timestamp > to_ms:
            continue
        clean[timestamp] = {"timestamp": timestamp, **values}
    return [clean[key] for key in sorted(clean)]


def sanitize_events(events: list[Any]) -> list[dict[str, Any]]:
    output = []
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            continue
        output.append({
            "id": str(event.get("id") or f"{event.get('time', '')}:{index}"),
            "timestamp": event.get("time"),
            "type": str(event.get("type") or "system")[:40],
            "message": str(event.get("message") or "")[:500],
        })
    return output


def parse_timestamp(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        try:
            return int(datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp() * 1000)
        except ValueError as exc:
            raise ValueError("invalid timestamp") from exc


class SlidingWindowRateLimiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests: dict[tuple[str, str], deque[float]] = defaultdict(deque)

    def allow(self, client: str, route: str, maximum: int, seconds: float = 1.0) -> bool:
        now = time.monotonic()
        key = (client, route)
        with self._lock:
            bucket = self._requests[key]
            while bucket and bucket[0] <= now - seconds:
                bucket.popleft()
            if len(bucket) >= maximum:
                return False
            bucket.append(now)
            return True

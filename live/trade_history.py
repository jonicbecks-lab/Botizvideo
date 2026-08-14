from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SUPPORTED = {"BTC", "ETH", "SOL"}
MAX_HISTORY_CAMPAIGNS = 40
MAX_FILL_BYTES = 8_000_000


def _coin(value: str) -> str:
    coin = value.upper().replace("USDT", "").replace("USD", "")
    if coin not in SUPPORTED:
        raise ValueError(f"Поддерживаются только BTC, ETH и SOL: {value}")
    return coin


def _iso_seconds(value: Any) -> int:
    text = str(value or "").strip()
    if not text:
        return 0
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp())
    except ValueError:
        return 0


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number == number and abs(number) != float("inf") else default


def _had_trade(campaign: dict[str, Any]) -> bool:
    if bool(campaign.get("hadPosition")):
        return True
    if int(campaign.get("cycleDeepest") or 0) > 0:
        return True
    return any(_finite(level.get("filledSize")) > 0 for level in campaign.get("levels", []))


def _campaign_net(campaign: dict[str, Any]) -> float:
    if campaign.get("netPnlApprox") is not None:
        return _finite(campaign.get("netPnlApprox"))
    gross = _finite(campaign.get("grossPnl"))
    fees = _finite(campaign.get("fees"))
    return gross - fees


def _read_campaigns(campaign_dir: Path, coin: str, limit: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not campaign_dir.is_dir():
        return rows
    for path in campaign_dir.glob("*.json"):
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 128_000:
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        if str(payload.get("coin") or "").upper() != coin:
            continue
        if not payload.get("completedAt") or not _had_trade(payload):
            continue
        rows.append(payload)
    rows.sort(key=lambda row: _iso_seconds(row.get("completedAt")), reverse=True)
    return rows[:limit]


def _read_selected_fills(path: Path, campaign_ids: set[str], coin: str) -> dict[str, list[dict[str, Any]]]:
    selected = {campaign_id: [] for campaign_id in campaign_ids}
    if not campaign_ids or not path.is_file() or path.is_symlink():
        return selected
    try:
        if path.stat().st_size > MAX_FILL_BYTES:
            return selected
        with path.open("r", encoding="utf-8") as handle:
            for raw in handle:
                try:
                    row = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                campaign_id = str(row.get("campaignId") or "")
                if campaign_id not in selected:
                    continue
                if str(row.get("coin") or "").upper() != coin:
                    continue
                selected[campaign_id].append(row)
    except (OSError, UnicodeDecodeError):
        pass
    return selected


def _weighted_price(rows: list[dict[str, Any]], fallback: float) -> float:
    total_size = sum(max(0.0, _finite(row.get("size"))) for row in rows)
    if total_size <= 0:
        return fallback
    notional = sum(
        max(0.0, _finite(row.get("size"))) * _finite(row.get("price"))
        for row in rows
    )
    return notional / total_size if notional > 0 else fallback


def build_chart_history(data_dir: Path, coin: str, limit: int = 24) -> dict[str, Any]:
    normalized = _coin(coin)
    safe_limit = max(1, min(int(limit), MAX_HISTORY_CAMPAIGNS))
    research_root = Path(data_dir) / "research"
    campaigns = _read_campaigns(research_root / "campaigns", normalized, safe_limit)
    campaign_ids = {str(row.get("campaignId") or "") for row in campaigns}
    fills = _read_selected_fills(research_root / "fills.jsonl", campaign_ids, normalized)

    markers: list[dict[str, Any]] = []
    for campaign in reversed(campaigns):
        campaign_id = str(campaign.get("campaignId") or "")
        campaign_fills = fills.get(campaign_id, [])
        by_level: dict[int, list[dict[str, Any]]] = {}
        exit_rows: list[dict[str, Any]] = []
        for row in campaign_fills:
            kind = str(row.get("ownerKind") or "")
            side = str(row.get("side") or "")
            level = int(row.get("ownerLevel") or 0)
            if kind == "entry" and side == "B" and level > 0:
                by_level.setdefault(level, []).append(row)
            elif kind == "target" and side == "A":
                exit_rows.append(row)

        for level in sorted(by_level):
            rows = by_level[level]
            exchange_times = [int(row.get("exchangeTimeMs") or 0) for row in rows]
            exchange_times = [value for value in exchange_times if value > 0]
            if not exchange_times:
                continue
            markers.append(
                {
                    "key": f"{campaign_id}:entry:{level}",
                    "campaignId": campaign_id,
                    "coin": normalized,
                    "kind": "entry",
                    "level": level,
                    "time": min(exchange_times) // 1000,
                    "price": _weighted_price(rows, 0.0),
                }
            )

        exit_time = 0
        exit_price = _finite(campaign.get("galkaPrice"))
        if exit_rows:
            exchange_times = [int(row.get("exchangeTimeMs") or 0) for row in exit_rows]
            exchange_times = [value for value in exchange_times if value > 0]
            if exchange_times:
                exit_time = max(exchange_times) // 1000
            exit_price = _weighted_price(exit_rows, exit_price)
        if not exit_time:
            exit_time = _iso_seconds(campaign.get("completedAt"))
        if exit_time:
            markers.append(
                {
                    "key": f"{campaign_id}:exit",
                    "campaignId": campaign_id,
                    "coin": normalized,
                    "kind": "exit",
                    "time": exit_time,
                    "price": exit_price,
                    "pnl": _campaign_net(campaign),
                    "deepest": int(campaign.get("cycleDeepest") or 0),
                    "status": campaign.get("status"),
                }
            )

    markers.sort(key=lambda row: (int(row.get("time") or 0), str(row.get("key") or "")))
    return {
        "coin": normalized,
        "campaignCount": len(campaigns),
        "markers": markers,
    }

from __future__ import annotations

import json
import math
import threading
import time
from collections import deque
from copy import deepcopy
from typing import Any

try:
    import websocket
except ImportError:  # pragma: no cover - surfaced through status()
    websocket = None

from .hyperliquid_gateway import INTERVAL_MS, SUPPORTED_COINS


BASE_PRICE_STEPS = {
    "BTC": 5.0,
    "ETH": 0.5,
    "SOL": 0.02,
}
RETENTION_MS = 12 * 60 * 60 * 1000
MAX_DEDUPE_IDS = 60_000
MAX_RETURN_CELLS = 1_500


def _now_ms() -> int:
    return int(time.time() * 1000)


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _percentile(values: list[float], quantile: float) -> float:
    rows = sorted(value for value in values if value >= 0 and math.isfinite(value))
    if not rows:
        return 0.0
    if len(rows) == 1:
        return rows[0]
    position = max(0.0, min(1.0, quantile)) * (len(rows) - 1)
    low = int(math.floor(position))
    high = int(math.ceil(position))
    if low == high:
        return rows[low]
    weight = position - low
    return rows[low] * (1.0 - weight) + rows[high] * weight


def _metric_summary(values: list[float]) -> dict[str, float | int]:
    rows = [value for value in values if value >= 0 and math.isfinite(value)]
    return {
        "count": len(rows),
        "q50": _percentile(rows, 0.50),
        "q75": _percentile(rows, 0.75),
        "q90": _percentile(rows, 0.90),
        "q95": _percentile(rows, 0.95),
        "q99": _percentile(rows, 0.99),
        "max": max(rows) if rows else 0.0,
    }


class ClusterVolumeService:
    """Research/display-only public trade stream for the chart cluster overlay.

    It has its own websocket thread and locks and never touches the trading action
    lock, order gateway or campaign decisions. Trades are compacted into one-minute
    price cells in memory; the browser asks for larger time/price aggregation.
    """

    def __init__(self, config: Any):
        self.url = (
            "wss://api.hyperliquid.xyz/ws"
            if bool(getattr(config, "mainnet", True))
            else "wss://api.hyperliquid-testnet.xyz/ws"
        )
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="galka-cluster-volume-ws",
            daemon=True,
        )
        self._started = False
        self._cells: dict[str, dict[tuple[int, int], dict[str, Any]]] = {
            coin: {} for coin in SUPPORTED_COINS
        }
        self._seen_ids: dict[str, set[str]] = {coin: set() for coin in SUPPORTED_COINS}
        self._seen_order: dict[str, deque[str]] = {
            coin: deque() for coin in SUPPORTED_COINS
        }
        self._connected = False
        self._last_message_ms = 0
        self._last_error: str | None = None
        self._reconnects = 0
        self._last_prune_ms = 0

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        if websocket is None:
            self._last_error = "websocket-client is not installed"
            return
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=3.0)

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "enabled": websocket is not None,
                "connected": self._connected,
                "lastMessageMs": self._last_message_ms or None,
                "lastError": self._last_error,
                "reconnects": self._reconnects,
                "cells": {coin: len(rows) for coin, rows in self._cells.items()},
                "retentionHours": RETENTION_MS / 3_600_000,
            }

    @staticmethod
    def _aggregation_multiplier(interval: str, aggregation: str) -> int:
        if aggregation == "fine":
            return 1
        if aggregation == "normal":
            return 2
        if aggregation == "coarse":
            return 5
        return {
            "1m": 1,
            "3m": 1,
            "5m": 2,
            "15m": 3,
            "30m": 5,
            "1h": 8,
            "2h": 10,
            "4h": 16,
            "8h": 24,
            "12h": 32,
            "1d": 48,
        }.get(interval, 2)

    def _dedupe_locked(self, coin: str, trade: dict[str, Any]) -> bool:
        trade_time = int(trade.get("time") or 0)
        tid = trade.get("tid")
        trade_hash = str(trade.get("hash") or "")
        unique = f"{trade_time}:{coin}:{tid if tid is not None else trade_hash}"
        seen = self._seen_ids[coin]
        if unique in seen:
            return False
        seen.add(unique)
        order = self._seen_order[coin]
        order.append(unique)
        while len(order) > MAX_DEDUPE_IDS:
            seen.discard(order.popleft())
        return True

    def _ingest_trade(self, trade: dict[str, Any]) -> None:
        coin = str(trade.get("coin") or "").upper()
        if coin not in SUPPORTED_COINS:
            return
        price = _finite(trade.get("px"))
        size = _finite(trade.get("sz"))
        timestamp = int(trade.get("time") or 0)
        side = str(trade.get("side") or "").upper()
        if price <= 0 or size <= 0 or timestamp <= 0 or side not in {"A", "B"}:
            return

        step = BASE_PRICE_STEPS[coin]
        minute = timestamp // 60_000 * 60_000
        price_index = int(math.floor(price / step))
        notional = price * size
        key = (minute, price_index)

        with self._lock:
            if not self._dedupe_locked(coin, trade):
                return
            cell = self._cells[coin].setdefault(
                key,
                {
                    "timeMs": minute,
                    "priceIndex": price_index,
                    "baseVolume": 0.0,
                    "quoteNotional": 0.0,
                    "buyNotional": 0.0,
                    "sellNotional": 0.0,
                    "buyBase": 0.0,
                    "sellBase": 0.0,
                    "tradeCount": 0,
                    "firstTradeMs": timestamp,
                    "lastTradeMs": timestamp,
                },
            )
            cell["baseVolume"] += size
            cell["quoteNotional"] += notional
            if side == "B":
                cell["buyNotional"] += notional
                cell["buyBase"] += size
            else:
                cell["sellNotional"] += notional
                cell["sellBase"] += size
            cell["tradeCount"] += 1
            cell["firstTradeMs"] = min(int(cell["firstTradeMs"]), timestamp)
            cell["lastTradeMs"] = max(int(cell["lastTradeMs"]), timestamp)
            self._last_message_ms = _now_ms()

            if self._last_message_ms - self._last_prune_ms >= 30_000:
                self._prune_locked(self._last_message_ms)

    def _prune_locked(self, now_ms: int) -> None:
        cutoff = now_ms - RETENTION_MS
        for coin in SUPPORTED_COINS:
            stale = [key for key in self._cells[coin] if key[0] < cutoff]
            for key in stale:
                self._cells[coin].pop(key, None)
        self._last_prune_ms = now_ms

    def snapshot(self, coin: str, interval: str, aggregation: str = "auto") -> dict[str, Any]:
        normalized = str(coin).upper().replace("USDT", "").replace("USD", "")
        if normalized not in SUPPORTED_COINS:
            raise ValueError(f"Unsupported cluster coin: {coin}")
        if interval not in INTERVAL_MS:
            raise ValueError(f"Unsupported cluster interval: {interval}")
        if aggregation not in {"auto", "fine", "normal", "coarse"}:
            raise ValueError(f"Unsupported cluster aggregation: {aggregation}")

        interval_ms = int(INTERVAL_MS[interval])
        base_step = BASE_PRICE_STEPS[normalized]
        multiplier = self._aggregation_multiplier(interval, aggregation)
        price_step = base_step * multiplier
        cutoff = _now_ms() - RETENTION_MS

        with self._lock:
            source = [deepcopy(row) for key, row in self._cells[normalized].items() if key[0] >= cutoff]
            status = {
                "connected": self._connected,
                "lastMessageMs": self._last_message_ms or None,
                "lastError": self._last_error,
            }

        grouped: dict[tuple[int, int], dict[str, Any]] = {}
        for row in source:
            bucket = int(row["timeMs"]) // interval_ms * interval_ms
            price = (
                float(row["quoteNotional"]) / float(row["baseVolume"])
                if float(row["baseVolume"]) > 0
                else (int(row["priceIndex"]) + 0.5) * base_step
            )
            price_group = int(math.floor(price / price_step))
            key = (bucket, price_group)
            target = grouped.setdefault(
                key,
                {
                    "timeMs": bucket,
                    "baseVolume": 0.0,
                    "quoteNotional": 0.0,
                    "buyNotional": 0.0,
                    "sellNotional": 0.0,
                    "buyBase": 0.0,
                    "sellBase": 0.0,
                    "tradeCount": 0,
                    "firstTradeMs": int(row["firstTradeMs"]),
                    "lastTradeMs": int(row["lastTradeMs"]),
                },
            )
            for field in (
                "baseVolume",
                "quoteNotional",
                "buyNotional",
                "sellNotional",
                "buyBase",
                "sellBase",
            ):
                target[field] += float(row[field])
            target["tradeCount"] += int(row["tradeCount"])
            target["firstTradeMs"] = min(target["firstTradeMs"], int(row["firstTradeMs"]))
            target["lastTradeMs"] = max(target["lastTradeMs"], int(row["lastTradeMs"]))

        cells: list[dict[str, Any]] = []
        for row in grouped.values():
            total = float(row["quoteNotional"])
            if total <= 0:
                continue
            price = total / max(float(row["baseVolume"]), 1e-12)
            buy = float(row["buyNotional"])
            sell = float(row["sellNotional"])
            cells.append(
                {
                    "time": int(row["timeMs"]) // 1000,
                    "timeMs": int(row["timeMs"]),
                    "price": price,
                    "totalNotional": total,
                    "buyNotional": buy,
                    "sellNotional": sell,
                    "deltaNotional": buy - sell,
                    "baseVolume": float(row["baseVolume"]),
                    "tradeCount": int(row["tradeCount"]),
                    "firstTradeMs": int(row["firstTradeMs"]),
                    "lastTradeMs": int(row["lastTradeMs"]),
                }
            )

        summary_by_metric = {
            "total": _metric_summary([float(row["totalNotional"]) for row in cells]),
            "buy": _metric_summary([float(row["buyNotional"]) for row in cells]),
            "sell": _metric_summary([float(row["sellNotional"]) for row in cells]),
            "delta": _metric_summary([abs(float(row["deltaNotional"])) for row in cells]),
        }
        summary = summary_by_metric["total"]

        if len(cells) > MAX_RETURN_CELLS:
            cells = sorted(cells, key=lambda row: float(row["totalNotional"]), reverse=True)[:MAX_RETURN_CELLS]
        cells.sort(key=lambda row: (int(row["timeMs"]), float(row["price"])))

        return {
            "coin": normalized,
            "interval": interval,
            "aggregation": aggregation,
            "priceStep": price_step,
            "retentionHours": RETENTION_MS / 3_600_000,
            "cells": cells,
            "summary": summary,
            "summaryByMetric": summary_by_metric,
            "stream": status,
            "serverTimeMs": _now_ms(),
        }

    def _run(self) -> None:
        backoff = 0.5
        while not self._stop.is_set():
            connection = None
            try:
                connection = websocket.create_connection(self.url, timeout=5, enable_multithread=False)
                connection.settimeout(1.0)
                for coin in sorted(SUPPORTED_COINS):
                    connection.send(
                        json.dumps(
                            {
                                "method": "subscribe",
                                "subscription": {"type": "trades", "coin": coin},
                            }
                        )
                    )
                with self._lock:
                    self._connected = True
                    self._last_error = None
                backoff = 0.5
                last_ping = time.monotonic()

                while not self._stop.is_set():
                    if time.monotonic() - last_ping >= 25.0:
                        connection.send(json.dumps({"method": "ping"}))
                        last_ping = time.monotonic()
                    try:
                        raw = connection.recv()
                    except websocket.WebSocketTimeoutException:
                        continue
                    if raw is None:
                        raise RuntimeError("cluster websocket closed")
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8")
                    message = json.loads(raw)
                    channel = str(message.get("channel") or "")
                    if channel in {"subscriptionResponse", "pong"}:
                        continue
                    if channel != "trades":
                        continue
                    data = message.get("data")
                    rows = data if isinstance(data, list) else [data]
                    for trade in rows:
                        if isinstance(trade, dict):
                            self._ingest_trade(trade)
            except Exception as exc:
                with self._lock:
                    self._connected = False
                    self._last_error = f"{type(exc).__name__}: {exc}"
                    self._reconnects += 1
                if self._stop.wait(backoff):
                    break
                backoff = min(10.0, backoff * 1.8)
            finally:
                with self._lock:
                    self._connected = False
                if connection is not None:
                    try:
                        connection.close()
                    except Exception:
                        pass

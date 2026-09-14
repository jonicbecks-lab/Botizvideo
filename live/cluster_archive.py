from __future__ import annotations

import json
import math
import os
import threading
import time
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .cluster_volume import (
    BASE_PRICE_STEPS,
    MAX_RETURN_CELLS,
    RETENTION_MS,
    ClusterVolumeService,
    _finite,
    _metric_summary,
)
from .hyperliquid_gateway import INTERVAL_MS, SUPPORTED_COINS


SEGMENT_MS = 6 * 60 * 60 * 1000
MAX_HISTORY_QUERY_MS = 90 * 24 * 60 * 60 * 1000
CHECKPOINT_INTERVAL_MS = 5_000
SCHEMA_VERSION = 1


def _now_ms() -> int:
    return int(time.time() * 1000)


def _compact(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "v": SCHEMA_VERSION,
        "t": int(row.get("timeMs") or 0),
        "p": int(row.get("priceIndex") or 0),
        "bv": float(row.get("baseVolume") or 0),
        "q": float(row.get("quoteNotional") or 0),
        "b": float(row.get("buyNotional") or 0),
        "s": float(row.get("sellNotional") or 0),
        "bb": float(row.get("buyBase") or 0),
        "sb": float(row.get("sellBase") or 0),
        "n": int(row.get("tradeCount") or 0),
        "f": int(row.get("firstTradeMs") or 0),
        "l": int(row.get("lastTradeMs") or 0),
    }


def _expand(row: dict[str, Any]) -> dict[str, Any] | None:
    try:
        minute = int(row.get("t") or 0)
        price_index = int(row.get("p") or 0)
        base_volume = float(row.get("bv") or 0)
        quote = float(row.get("q") or 0)
        if minute <= 0 or price_index < 0 or base_volume <= 0 or quote <= 0:
            return None
        return {
            "timeMs": minute,
            "priceIndex": price_index,
            "baseVolume": base_volume,
            "quoteNotional": quote,
            "buyNotional": float(row.get("b") or 0),
            "sellNotional": float(row.get("s") or 0),
            "buyBase": float(row.get("bb") or 0),
            "sellBase": float(row.get("sb") or 0),
            "tradeCount": int(row.get("n") or 0),
            "firstTradeMs": int(row.get("f") or minute),
            "lastTradeMs": int(row.get("l") or minute),
        }
    except (TypeError, ValueError):
        return None


def _version(row: dict[str, Any]) -> tuple[int, int, float]:
    return (
        int(row.get("lastTradeMs") or 0),
        int(row.get("tradeCount") or 0),
        float(row.get("quoteNotional") or 0),
    )


class PersistentClusterVolumeService(ClusterVolumeService):
    """Cluster stream with compact local archive suitable for Git synchronization.

    The exchange websocket remains isolated from trading. Completed minute/price
    cells are appended to six-hour JSONL segments. The current minute is atomically
    checkpointed so a normal app restart does not erase the newest cluster data.
    Historical chart requests merge archive rows with the live in-memory cells.
    """

    def __init__(self, config: Any):
        super().__init__(config)
        data_dir = Path(getattr(config, "data_dir"))
        self.archive_root = data_dir / "research" / "clusters" / "archive"
        self.checkpoint_root = data_dir / "research" / "clusters" / "current"
        self._archive_lock = threading.RLock()
        self._archive_cache: dict[Path, tuple[int, int, list[dict[str, Any]]]] = {}
        self._persisted_versions: dict[tuple[str, int, int], tuple[int, int, float]] = {}
        self._last_checkpoint_ms: dict[str, int] = {coin: 0 for coin in SUPPORTED_COINS}
        self._last_wall_minute: dict[str, int] = {
            coin: _now_ms() // 60_000 * 60_000 for coin in SUPPORTED_COINS
        }
        self._archive_stats_cache: tuple[int, dict[str, Any]] | None = None
        try:
            self.archive_root.mkdir(parents=True, exist_ok=True)
            self.checkpoint_root.mkdir(parents=True, exist_ok=True)
            os.chmod(self.archive_root.parent, 0o700)
            self._load_recent_local()
        except Exception as exc:
            self._last_error = f"cluster archive init: {type(exc).__name__}: {exc}"

    @staticmethod
    def _segment_start_ms(value_ms: int) -> int:
        return value_ms // SEGMENT_MS * SEGMENT_MS

    def _segment_path(self, coin: str, value_ms: int) -> Path:
        start = self._segment_start_ms(value_ms)
        dt = datetime.fromtimestamp(start / 1000.0, tz=timezone.utc)
        return self.archive_root / coin / f"{dt:%Y-%m-%d}-{dt.hour:02d}.jsonl"

    def _segment_paths(self, coin: str, from_ms: int, to_ms: int) -> list[Path]:
        start = self._segment_start_ms(from_ms)
        end = self._segment_start_ms(to_ms)
        paths: list[Path] = []
        cursor = start
        while cursor <= end:
            paths.append(self._segment_path(coin, cursor))
            cursor += SEGMENT_MS
        return paths

    def _read_segment(self, path: Path) -> list[dict[str, Any]]:
        try:
            stat = path.stat()
        except OSError:
            return []
        cached = self._archive_cache.get(path)
        signature = (int(stat.st_mtime_ns), int(stat.st_size))
        if cached and cached[:2] == signature:
            return [deepcopy(row) for row in cached[2]]

        latest: dict[tuple[int, int], dict[str, Any]] = {}
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        packed = json.loads(line)
                    except Exception:
                        continue
                    if not isinstance(packed, dict):
                        continue
                    row = _expand(packed)
                    if not row:
                        continue
                    key = (int(row["timeMs"]), int(row["priceIndex"]))
                    previous = latest.get(key)
                    if previous is None or _version(row) > _version(previous):
                        latest[key] = row
        except OSError:
            return []
        rows = sorted(latest.values(), key=lambda row: (int(row["timeMs"]), int(row["priceIndex"])))
        self._archive_cache[path] = (signature[0], signature[1], rows)
        return [deepcopy(row) for row in rows]

    def _read_archive_rows(self, coin: str, from_ms: int, to_ms: int) -> list[dict[str, Any]]:
        latest: dict[tuple[int, int], dict[str, Any]] = {}
        with self._archive_lock:
            for path in self._segment_paths(coin, from_ms, to_ms):
                for row in self._read_segment(path):
                    minute = int(row["timeMs"])
                    if minute < from_ms - 60_000 or minute > to_ms + 60_000:
                        continue
                    key = (minute, int(row["priceIndex"]))
                    previous = latest.get(key)
                    if previous is None or _version(row) > _version(previous):
                        latest[key] = row
        return list(latest.values())

    def _append_rows(self, coin: str, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        by_path: dict[Path, list[dict[str, Any]]] = {}
        for row in rows:
            key = (coin, int(row["timeMs"]), int(row["priceIndex"]))
            version = _version(row)
            if version <= self._persisted_versions.get(key, (0, 0, 0.0)):
                continue
            by_path.setdefault(self._segment_path(coin, int(row["timeMs"])), []).append(row)

        with self._archive_lock:
            for path, path_rows in by_path.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as handle:
                    for row in path_rows:
                        handle.write(json.dumps(_compact(row), separators=(",", ":"), allow_nan=False))
                        handle.write("\n")
                    handle.flush()
                try:
                    os.chmod(path, 0o600)
                except OSError:
                    pass
                self._archive_cache.pop(path, None)
                for row in path_rows:
                    key = (coin, int(row["timeMs"]), int(row["priceIndex"]))
                    self._persisted_versions[key] = _version(row)
            self._archive_stats_cache = None

    def _checkpoint_coin(self, coin: str, force: bool = False) -> None:
        now = _now_ms()
        if not force and now - self._last_checkpoint_ms.get(coin, 0) < CHECKPOINT_INTERVAL_MS:
            return
        current_minute = now // 60_000 * 60_000
        with self._lock:
            rows = [
                deepcopy(row)
                for (minute, _index), row in self._cells[coin].items()
                if minute == current_minute
            ]
        payload = {
            "schemaVersion": SCHEMA_VERSION,
            "coin": coin,
            "minute": current_minute,
            "writtenAtMs": now,
            "rows": [_compact(row) for row in rows],
        }
        path = self.checkpoint_root / f"{coin}.json"
        temporary = path.with_suffix(".json.tmp")
        try:
            with self._archive_lock:
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
                try:
                    os.chmod(temporary, 0o600)
                except OSError:
                    pass
                os.replace(temporary, path)
            self._last_checkpoint_ms[coin] = now
        except OSError:
            pass

    def _load_checkpoint(self, coin: str) -> list[dict[str, Any]]:
        path = self.checkpoint_root / f"{coin}.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []
        rows = payload.get("rows") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            return []
        output: list[dict[str, Any]] = []
        for packed in rows:
            if isinstance(packed, dict):
                row = _expand(packed)
                if row:
                    output.append(row)
        return output

    def _merge_memory_row(self, coin: str, row: dict[str, Any]) -> None:
        key = (int(row["timeMs"]), int(row["priceIndex"]))
        previous = self._cells[coin].get(key)
        if previous is None or _version(row) > _version(previous):
            self._cells[coin][key] = deepcopy(row)

    def _load_recent_local(self) -> None:
        now = _now_ms()
        from_ms = now - RETENTION_MS
        with self._lock:
            for coin in SUPPORTED_COINS:
                for row in self._read_archive_rows(coin, from_ms, now):
                    self._merge_memory_row(coin, row)
                    key = (coin, int(row["timeMs"]), int(row["priceIndex"]))
                    self._persisted_versions[key] = _version(row)
                for row in self._load_checkpoint(coin):
                    if int(row["timeMs"]) >= from_ms:
                        self._merge_memory_row(coin, row)

    def _persist_finished_before(self, coin: str, current_minute: int) -> None:
        with self._lock:
            rows = [
                deepcopy(row)
                for (minute, _index), row in self._cells[coin].items()
                if minute < current_minute
            ]
        self._append_rows(coin, rows)

    def _ingest_trade(self, trade: dict[str, Any]) -> None:
        coin = str(trade.get("coin") or "").upper()
        timestamp = int(trade.get("time") or 0)
        super()._ingest_trade(trade)
        if coin not in SUPPORTED_COINS or timestamp <= 0:
            return

        wall_minute = _now_ms() // 60_000 * 60_000
        previous_wall = self._last_wall_minute.get(coin, wall_minute)
        if wall_minute > previous_wall:
            self._persist_finished_before(coin, wall_minute)
            self._last_wall_minute[coin] = wall_minute

        trade_minute = timestamp // 60_000 * 60_000
        if trade_minute < wall_minute:
            price = _finite(trade.get("px"))
            if price > 0:
                index = int(math.floor(price / BASE_PRICE_STEPS[coin]))
                with self._lock:
                    row = deepcopy(self._cells[coin].get((trade_minute, index)))
                if row:
                    self._append_rows(coin, [row])
        self._checkpoint_coin(coin)

    def stop(self) -> None:
        wall_minute = _now_ms() // 60_000 * 60_000
        for coin in SUPPORTED_COINS:
            try:
                self._persist_finished_before(coin, wall_minute)
                self._checkpoint_coin(coin, force=True)
            except Exception:
                pass
        super().stop()

    def _archive_stats(self) -> dict[str, Any]:
        now = _now_ms()
        cached = self._archive_stats_cache
        if cached and now - cached[0] < 30_000:
            return dict(cached[1])
        files = 0
        total_bytes = 0
        earliest: int | None = None
        latest: int | None = None
        try:
            for path in self.archive_root.glob("*/*.jsonl"):
                if not path.is_file() or path.is_symlink():
                    continue
                files += 1
                total_bytes += path.stat().st_size
                rows = self._read_segment(path)
                if rows:
                    first = min(int(row["timeMs"]) for row in rows)
                    last = max(int(row["timeMs"]) for row in rows)
                    earliest = first if earliest is None else min(earliest, first)
                    latest = last if latest is None else max(latest, last)
        except OSError:
            pass
        result = {
            "files": files,
            "bytes": total_bytes,
            "earliestMs": earliest,
            "latestMs": latest,
            "segmentHours": SEGMENT_MS / 3_600_000,
            "githubSync": "data/galka-live-journal/datasets/live/clusters",
        }
        self._archive_stats_cache = (now, result)
        return dict(result)

    def status(self) -> dict[str, Any]:
        result = super().status()
        result["archive"] = self._archive_stats()
        result["archive"]["localPath"] = str(self.archive_root)
        return result

    def snapshot(
        self,
        coin: str,
        interval: str,
        aggregation: str = "auto",
        from_ms: int | None = None,
        to_ms: int | None = None,
    ) -> dict[str, Any]:
        normalized = str(coin).upper().replace("USDT", "").replace("USD", "")
        if normalized not in SUPPORTED_COINS:
            raise ValueError(f"Unsupported cluster coin: {coin}")
        if interval not in INTERVAL_MS:
            raise ValueError(f"Unsupported cluster interval: {interval}")
        if aggregation not in {"auto", "fine", "normal", "coarse"}:
            raise ValueError(f"Unsupported cluster aggregation: {aggregation}")

        now = _now_ms()
        end_ms = int(to_ms or now)
        start_ms = int(from_ms or max(0, end_ms - RETENTION_MS))
        if start_ms <= 0 or end_ms <= 0 or start_ms > end_ms:
            raise ValueError("Invalid cluster history range")
        if end_ms - start_ms > MAX_HISTORY_QUERY_MS:
            raise ValueError("Cluster history request is limited to 90 days")

        source_by_key: dict[tuple[int, int], dict[str, Any]] = {}
        for row in self._read_archive_rows(normalized, start_ms, end_ms):
            key = (int(row["timeMs"]), int(row["priceIndex"]))
            source_by_key[key] = row
        with self._lock:
            live_rows = [
                deepcopy(row)
                for (minute, _index), row in self._cells[normalized].items()
                if minute >= start_ms - 60_000 and minute <= end_ms + 60_000
            ]
            stream = {
                "connected": self._connected,
                "lastMessageMs": self._last_message_ms or None,
                "lastError": self._last_error,
            }
        for row in live_rows:
            key = (int(row["timeMs"]), int(row["priceIndex"]))
            previous = source_by_key.get(key)
            if previous is None or _version(row) >= _version(previous):
                source_by_key[key] = row
        source = list(source_by_key.values())

        interval_ms = int(INTERVAL_MS[interval])
        base_step = BASE_PRICE_STEPS[normalized]
        multiplier = self._aggregation_multiplier(interval, aggregation)
        price_step = base_step * multiplier
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
            for field in ("baseVolume", "quoteNotional", "buyNotional", "sellNotional", "buyBase", "sellBase"):
                target[field] += float(row[field])
            target["tradeCount"] += int(row["tradeCount"])
            target["firstTradeMs"] = min(target["firstTradeMs"], int(row["firstTradeMs"]))
            target["lastTradeMs"] = max(target["lastTradeMs"], int(row["lastTradeMs"]))

        cells: list[dict[str, Any]] = []
        for row in grouped.values():
            total = float(row["quoteNotional"])
            if total <= 0:
                continue
            base_volume = max(float(row["baseVolume"]), 1e-12)
            buy = float(row["buyNotional"])
            sell = float(row["sellNotional"])
            cells.append(
                {
                    "time": int(row["timeMs"]) // 1000,
                    "timeMs": int(row["timeMs"]),
                    "price": total / base_volume,
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
        if len(cells) > MAX_RETURN_CELLS:
            cells = sorted(cells, key=lambda row: float(row["totalNotional"]), reverse=True)[:MAX_RETURN_CELLS]
        cells.sort(key=lambda row: (int(row["timeMs"]), float(row["price"])))

        return {
            "coin": normalized,
            "interval": interval,
            "aggregation": aggregation,
            "priceStep": price_step,
            "memoryRetentionHours": RETENTION_MS / 3_600_000,
            "archivePersistent": True,
            "range": {"fromMs": start_ms, "toMs": end_ms},
            "cells": cells,
            "summary": summary_by_metric["total"],
            "summaryByMetric": summary_by_metric,
            "stream": stream,
            "archive": self._archive_stats(),
            "serverTimeMs": now,
        }

from __future__ import annotations

import hashlib
import json
import math
import os
import queue
import statistics
import threading
import time
from collections import deque
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

try:
    import websocket
except ImportError:  # pragma: no cover
    websocket = None


ACTIVE_CAMPAIGN_STATUSES = {
    "placing",
    "waiting",
    "open",
    "closing",
    "canceling",
    "emergency",
    "recovery",
}
POST_RETURN_HORIZONS_MS = (1_000, 3_000, 5_000, 10_000, 30_000, 60_000, 120_000, 300_000)
SCHEMA_VERSION = 1


def _now_ms() -> int:
    return int(time.time() * 1000)


def _now_ns() -> int:
    return time.time_ns()


def _iso_from_ms(value: int | float | None) -> str | None:
    if not value:
        return None
    millis = int(value)
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(millis / 1000.0)) + f".{millis % 1000:03d}Z"


def _iso_now() -> str:
    return _iso_from_ms(_now_ms()) or ""


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    try:
        json.dumps(value, allow_nan=False)
        return value
    except Exception:
        return str(value)


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".tmp-{os.getpid()}-{threading.get_ident()}")
    temporary.write_text(
        json.dumps(_json_safe(payload), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    os.replace(temporary, path)


def _percentile(values: list[float], quantile: float) -> float:
    rows = sorted(value for value in values if value >= 0 and math.isfinite(value))
    if not rows:
        return 0.0
    if len(rows) == 1:
        return rows[0]
    position = max(0.0, min(1.0, quantile)) * (len(rows) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return rows[lower]
    weight = position - lower
    return rows[lower] * (1.0 - weight) + rows[upper] * weight


def _parse_iso_ms(value: Any) -> int:
    text = str(value or "").strip()
    if not text:
        return 0
    try:
        from datetime import datetime

        return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp() * 1000)
    except Exception:
        return 0


@dataclass(frozen=True)
class RecorderSettings:
    enabled: bool
    root: Path
    l2_depth: int
    windows_ms: tuple[int, ...]
    feature_interval_ms: int
    book_bps: tuple[float, ...]
    imbalance_ratio: float
    stacked_levels: int
    large_trade_quantile: float
    baseline_seconds: int
    footprint_price_step: float
    queue_max: int
    flush_interval_seconds: float = 1.0
    fsync_interval_seconds: float = 5.0

    @classmethod
    def from_config(cls, config: Any) -> "RecorderSettings":
        return cls(
            enabled=bool(getattr(config, "research_recorder_enabled", False)),
            root=Path(
                getattr(
                    config,
                    "research_recorder_dir",
                    Path(config.data_dir) / "research" / "galka_campaigns",
                )
            ),
            l2_depth=int(getattr(config, "research_l2_depth", 20)),
            windows_ms=tuple(
                int(value)
                for value in getattr(
                    config,
                    "research_windows_ms",
                    (100, 250, 500, 1000, 5000, 10000),
                )
            ),
            feature_interval_ms=int(getattr(config, "research_feature_interval_ms", 100)),
            book_bps=tuple(
                float(value)
                for value in getattr(
                    config,
                    "research_book_bps",
                    (1.0, 2.5, 5.0, 10.0, 25.0),
                )
            ),
            imbalance_ratio=float(getattr(config, "research_imbalance_ratio", 3.0)),
            stacked_levels=int(getattr(config, "research_stacked_levels", 3)),
            large_trade_quantile=float(getattr(config, "research_large_trade_quantile", 0.95)),
            baseline_seconds=int(getattr(config, "research_baseline_seconds", 60)),
            footprint_price_step=float(getattr(config, "research_footprint_price_step", 0.0)),
            queue_max=int(getattr(config, "research_queue_max", 50_000)),
        )


class ResearchSession:
    """One campaign-scoped recorder; all disk I/O happens on its worker thread."""

    def __init__(self, settings: RecorderSettings, campaign: dict[str, Any]):
        self.settings = settings
        self.campaign_id = str(campaign.get("id") or "")
        self.coin = str(campaign.get("coin") or "").upper()
        self.galka = _finite(campaign.get("galkaPrice"))
        self.path = settings.root / self.coin / self.campaign_id
        self.metadata_path = self.path / "metadata.json"
        self.events_path = self.path / "events.jsonl"
        self.trades_path = self.path / "trades.raw.jsonl"
        self.book_path = self.path / "orderbook.raw.jsonl"
        self.features_path = self.path / "features.jsonl"
        self.footprint_path = self.path / "footprint.json"
        self.manifest_path = self.path / "dataset_manifest.json"
        self._queue: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=settings.queue_max)
        self._thread = threading.Thread(
            target=self._run,
            name=f"galka-research-{self.coin}-{self.campaign_id[-6:]}",
            daemon=True,
        )
        self._stop = threading.Event()
        self._finalize_requested = threading.Event()
        self._started = False
        self._state_lock = threading.RLock()
        self._dropped = 0
        self._writer_error: str | None = None

        self._trade_window: deque[tuple[int, int, str, float, float]] = deque()
        self._cvd = 0.0
        self._previous_delta: dict[int, float] = {}
        self._last_feature_ns = 0
        self._large_threshold = 0.0
        self._large_threshold_updated_ns = 0
        self._last_book: dict[str, dict[float, tuple[float, int]]] = {"B": {}, "A": {}}
        self._book_change_metrics: dict[str, float] = {}
        self._last_removed: dict[tuple[str, float], int] = {}
        self._replenishment_count: dict[tuple[str, float], int] = {}
        self._footprint: dict[float, dict[str, float]] = {}

        self.metadata = self._load_or_create_metadata(campaign)
        crossed = self.metadata.get("crossedBelowGalka") or {}
        returned = self.metadata.get("returnToGalka") or {}
        self.crossed_below_ms = int(crossed.get("exchangeTimestampMs") or 0)
        self.return_to_galka_ms = int(returned.get("exchangeTimestampMs") or 0)
        self.recording = bool(self.metadata.get("recordingStartedAt"))
        self.campaign_completed_ms = int(self.metadata.get("campaignCompletedAtMs") or 0)
        self.post_outcome_done = bool((self.metadata.get("postReturnOutcome") or {}).get("complete"))
        self.minimum_observed_price = _finite(self.metadata.get("minimumObservedPrice"), 0.0)
        self.maximum_observed_price = _finite(self.metadata.get("maximumObservedPrice"), 0.0)
        self._post_prices: list[tuple[int, float]] = []

    def _load_or_create_metadata(self, campaign: dict[str, Any]) -> dict[str, Any]:
        existing: dict[str, Any] = {}
        try:
            if self.metadata_path.is_file() and not self.metadata_path.is_symlink():
                parsed = json.loads(self.metadata_path.read_text(encoding="utf-8"))
                if isinstance(parsed, dict):
                    existing = parsed
        except Exception:
            existing = {}
        levels = self._level_snapshot(campaign)
        base = {
            "schemaVersion": SCHEMA_VERSION,
            "module": "Galka Research Recorder",
            "campaignId": self.campaign_id,
            "symbol": self.coin,
            "galkaLevel": self.galka,
            "campaignActivatedAt": campaign.get("createdAt"),
            "campaignActivatedAtMs": campaign.get("createdMs"),
            "status": "armed",
            "recordingStartedAt": None,
            "recordingStoppedAt": None,
            "crossedBelowGalka": None,
            "returnToGalka": None,
            "campaignCompletedAt": campaign.get("completedAt"),
            "campaignCompletedAtMs": _parse_iso_ms(campaign.get("completedAt")),
            "maxDeviationBelowGalkaPct": 0.0,
            "minimumObservedPrice": None,
            "maximumObservedPrice": None,
            "levels": levels,
            "executions": [],
            "actualRealizedPnl": None,
            "fees": None,
            "researchOnly": True,
            "tradingLogicChanged": False,
            "rawFormat": "append-only JSONL; materialize Parquet later if desired",
            "postReturnHorizonsMs": list(POST_RETURN_HORIZONS_MS),
            "postReturnOutcome": {"complete": False, "horizons": {}},
            "stream": {"reconnects": 0, "gaps": [], "droppedQueueMessages": 0, "writerError": None},
            "settings": {
                "l2Depth": self.settings.l2_depth,
                "windowsMs": list(self.settings.windows_ms),
                "featureIntervalMs": self.settings.feature_interval_ms,
                "bookBandsBps": list(self.settings.book_bps),
                "imbalanceRatioThreshold": self.settings.imbalance_ratio,
                "stackedLevelsThreshold": self.settings.stacked_levels,
                "largeTradeQuantile": self.settings.large_trade_quantile,
                "baselineSeconds": self.settings.baseline_seconds,
                "footprintPriceStep": self.settings.footprint_price_step,
            },
            "updatedAt": _iso_now(),
        }
        if existing:
            base.update(existing)
            base["levels"] = levels or existing.get("levels", [])
            base["updatedAt"] = _iso_now()
        return base

    @staticmethod
    def _level_snapshot(campaign: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {
                "index": row.get("index"),
                "depthPct": row.get("depth_pct"),
                "price": row.get("price"),
                "size": row.get("size"),
                "notional": row.get("notional"),
                "entryOid": row.get("oid"),
                "targetOid": row.get("tpOid"),
                "entryCloid": row.get("entryCloid"),
                "targetCloid": row.get("targetCloid"),
                "filledSize": row.get("filledSize"),
                "averageFillPrice": row.get("averageFillPrice"),
                "status": row.get("status"),
            }
            for row in campaign.get("levels", [])
        ]

    def start(self) -> None:
        if not self._started:
            self._started = True
            self._thread.start()

    def enqueue(self, kind: str, payload: Any) -> None:
        if self._stop.is_set():
            return
        try:
            self._queue.put_nowait((kind, payload))
        except queue.Full:
            with self._state_lock:
                self._dropped += 1

    def mark_crossed_below(self, exchange_ms: int, receive_ns: int, price: float, basis: str) -> bool:
        with self._state_lock:
            if self.crossed_below_ms:
                return False
            self.crossed_below_ms = int(exchange_ms or receive_ns // 1_000_000)
            self.recording = True
            self.metadata["crossedBelowGalka"] = {
                "exchangeTimestampMs": self.crossed_below_ms,
                "exchangeTimestamp": _iso_from_ms(self.crossed_below_ms),
                "localReceiveTimestampNs": int(receive_ns),
                "localReceiveTimestampMs": int(receive_ns // 1_000_000),
                "price": price,
                "basis": basis,
            }
            self.metadata["recordingStartedAt"] = _iso_from_ms(receive_ns // 1_000_000)
            self.metadata["status"] = "recording"
        self.enqueue("event", {"event": "crossed_below_galka", **self.metadata["crossedBelowGalka"]})
        self.enqueue("metadata", None)
        return True

    def mark_return_to_galka(self, exchange_ms: int, receive_ns: int, price: float, basis: str) -> bool:
        with self._state_lock:
            if not self.crossed_below_ms or self.return_to_galka_ms:
                return False
            self.return_to_galka_ms = int(exchange_ms or receive_ns // 1_000_000)
            self.metadata["returnToGalka"] = {
                "exchangeTimestampMs": self.return_to_galka_ms,
                "exchangeTimestamp": _iso_from_ms(self.return_to_galka_ms),
                "localReceiveTimestampNs": int(receive_ns),
                "localReceiveTimestampMs": int(receive_ns // 1_000_000),
                "price": price,
                "basis": basis,
            }
            self._post_prices = [(self.return_to_galka_ms, price)]
        self.enqueue("event", {"event": "return_to_galka", **self.metadata["returnToGalka"]})
        self.enqueue("metadata", None)
        return True

    def observe_price(self, exchange_ms: int, price: float) -> None:
        if exchange_ms <= 0 or price <= 0:
            return
        with self._state_lock:
            if self.minimum_observed_price <= 0 or price < self.minimum_observed_price:
                self.minimum_observed_price = price
            if price > self.maximum_observed_price:
                self.maximum_observed_price = price
            self.metadata["minimumObservedPrice"] = self.minimum_observed_price
            self.metadata["maximumObservedPrice"] = self.maximum_observed_price
            if self.galka > 0 and self.minimum_observed_price > 0:
                self.metadata["maxDeviationBelowGalkaPct"] = max(
                    0.0,
                    (self.galka - self.minimum_observed_price) / self.galka * 100.0,
                )
            if self.return_to_galka_ms and exchange_ms >= self.return_to_galka_ms:
                self._post_prices.append((exchange_ms, price))
                self._update_post_outcome_locked(exchange_ms)

    def _update_post_outcome_locked(self, now_exchange_ms: int) -> None:
        if not self.return_to_galka_ms or not self._post_prices:
            return
        outcome = self.metadata.setdefault("postReturnOutcome", {"complete": False, "horizons": {}})
        horizons = outcome.setdefault("horizons", {})
        start_price = self._post_prices[0][1]
        for horizon in POST_RETURN_HORIZONS_MS:
            key = str(horizon)
            if key in horizons:
                continue
            target = self.return_to_galka_ms + horizon
            if now_exchange_ms < target:
                continue
            observed = [row for row in self._post_prices if self.return_to_galka_ms <= row[0] <= target]
            if not observed:
                continue
            endpoint = min(observed, key=lambda row: abs(row[0] - target))
            maximum = max(observed, key=lambda row: row[1])
            minimum = min(observed, key=lambda row: row[1])
            horizons[key] = {
                "targetTimestampMs": target,
                "observedTimestampMs": endpoint[0],
                "price": endpoint[1],
                "startPrice": start_price,
                "mfeAbs": maximum[1] - start_price,
                "maeAbs": minimum[1] - start_price,
                "mfePct": ((maximum[1] / start_price) - 1.0) * 100.0 if start_price else None,
                "maePct": ((minimum[1] / start_price) - 1.0) * 100.0 if start_price else None,
                "maximumPrice": maximum[1],
                "minimumPrice": minimum[1],
                "maximumTimestampMs": maximum[0],
                "minimumTimestampMs": minimum[0],
                "timeToMaximumMs": maximum[0] - self.return_to_galka_ms,
            }
        if str(max(POST_RETURN_HORIZONS_MS)) in horizons:
            outcome["complete"] = True
            outcome["completedAt"] = _iso_from_ms(now_exchange_ms)
            self.post_outcome_done = True
            self.enqueue("metadata", None)

    def post_outcome_pending(self, now_ms: int | None = None) -> bool:
        with self._state_lock:
            if self.post_outcome_done or not self.return_to_galka_ms:
                return False
            current = int(now_ms or _now_ms())
            return current < self.return_to_galka_ms + max(POST_RETURN_HORIZONS_MS) + 10_000

    def update_campaign(self, campaign: dict[str, Any], reason: str) -> None:
        with self._state_lock:
            self.metadata["campaignStatus"] = campaign.get("status")
            self.metadata["campaignCompletedAt"] = campaign.get("completedAt")
            completed_ms = _parse_iso_ms(campaign.get("completedAt"))
            if completed_ms:
                self.campaign_completed_ms = completed_ms
                self.metadata["campaignCompletedAtMs"] = completed_ms
            gross = float(campaign.get("cycleClosedPnl") or 0) + float(campaign.get("l1RealizedPnl") or 0)
            self.metadata["actualRealizedPnl"] = (
                campaign.get("finalClosedPnl") if campaign.get("finalClosedPnl") is not None else gross
            )
            self.metadata["fees"] = float(campaign.get("cycleFees") or 0)
            self.metadata["levels"] = self._level_snapshot(campaign)
            self.metadata["lastCampaignUpdateReason"] = reason
            self.metadata["updatedAt"] = _iso_now()
        self.enqueue("metadata", None)

    def record_execution(self, fill: dict[str, Any]) -> None:
        row = {
            "event": "galka_execution",
            "exchangeTimestampMs": int(fill.get("time") or 0),
            "localReceiveTimestampNs": _now_ns(),
            "ownerKind": fill.get("_ownerKind"),
            "ownerLevel": fill.get("_ownerLevel"),
            "oid": fill.get("oid"),
            "cloid": fill.get("cloid") or fill.get("_resolvedCloid"),
            "side": fill.get("side"),
            "price": fill.get("price"),
            "size": fill.get("size"),
            "fee": fill.get("fee"),
            "closedPnl": fill.get("closedPnl"),
            "hash": fill.get("hash"),
        }
        with self._state_lock:
            executions = self.metadata.setdefault("executions", [])
            executions.append(deepcopy(row))
            del executions[:-500]
        self.enqueue("event", row)
        self.enqueue("metadata", None)

    def record_galka_event(self, event_type: str, message: str, meta: dict[str, Any]) -> None:
        self.enqueue(
            "event",
            {
                "event": "galka_event",
                "recordedAt": _iso_now(),
                "localReceiveTimestampNs": _now_ns(),
                "type": event_type,
                "message": message,
                "meta": deepcopy(meta),
            },
        )

    def record_stream_gap(self, message: str, started_ms: int | None = None, ended_ms: int | None = None) -> None:
        with self._state_lock:
            stream = self.metadata.setdefault("stream", {})
            stream["reconnects"] = int(stream.get("reconnects") or 0) + 1
            gaps = stream.setdefault("gaps", [])
            gaps.append(
                {
                    "message": message,
                    "startedAtMs": started_ms,
                    "endedAtMs": ended_ms,
                    "recordedAt": _iso_now(),
                }
            )
            del gaps[:-100]
        self.enqueue("metadata", None)

    def complete_campaign(self, campaign: dict[str, Any]) -> None:
        self.update_campaign(campaign, "campaign_completed")
        with self._state_lock:
            self.metadata["recordingStoppedAt"] = _iso_now()
            self.metadata["status"] = "post_return_pending" if self.post_outcome_pending() else "completed"
        self.enqueue(
            "event",
            {
                "event": "campaign_completed",
                "recordedAt": _iso_now(),
                "campaignStatus": campaign.get("status"),
                "actualRealizedPnl": self.metadata.get("actualRealizedPnl"),
                "fees": self.metadata.get("fees"),
            },
        )
        self.enqueue("metadata", None)
        if not self.post_outcome_pending():
            self.request_finalize()

    def request_finalize(self) -> None:
        self._finalize_requested.set()
        self.enqueue("finalize", None)

    def shutdown_flush(self) -> None:
        self.enqueue("event", {"event": "shutdown_flush", "recordedAt": _iso_now()})
        self._stop.set()
        try:
            self._queue.put_nowait(("stop", None))
        except queue.Full:
            pass
        if self._thread.is_alive():
            self._thread.join(timeout=4.0)

    @property
    def finalized(self) -> bool:
        return self._finalize_requested.is_set()

    def _open_handle(self, path: Path, handles: dict[Path, Any]):
        handle = handles.get(path)
        if handle is None:
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = path.open("a", encoding="utf-8")
            handles[path] = handle
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        return handle

    def _append(self, path: Path, payload: dict[str, Any], handles: dict[Path, Any]) -> None:
        handle = self._open_handle(path, handles)
        handle.write(json.dumps(_json_safe(payload), ensure_ascii=False, separators=(",", ":"), allow_nan=False))
        handle.write("\n")

    def _run(self) -> None:
        handles: dict[Path, Any] = {}
        last_flush = time.monotonic()
        last_fsync = last_flush
        try:
            self.path.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(self.path, 0o700)
            except OSError:
                pass
            _atomic_json(self.metadata_path, self.metadata)
            while True:
                item: tuple[str, Any] | None = None
                try:
                    item = self._queue.get(timeout=0.05)
                except queue.Empty:
                    pass
                if item is not None:
                    kind, payload = item
                    if kind == "trade":
                        self._process_trade(payload, handles)
                    elif kind == "book":
                        self._process_book(payload, handles)
                    elif kind == "event":
                        self._append(self.events_path, payload, handles)
                    elif kind == "metadata":
                        self._write_metadata()
                    elif kind == "finalize":
                        self._finalize(handles)
                    elif kind == "stop" and self._queue.empty():
                        break

                now_ns = _now_ns()
                if self.recording and not self.campaign_completed_ms:
                    self._maybe_feature(now_ns, handles)

                monotonic_now = time.monotonic()
                if monotonic_now - last_flush >= self.settings.flush_interval_seconds:
                    for handle in handles.values():
                        handle.flush()
                    last_flush = monotonic_now
                if monotonic_now - last_fsync >= self.settings.fsync_interval_seconds:
                    for handle in handles.values():
                        try:
                            os.fsync(handle.fileno())
                        except OSError:
                            pass
                    self._write_metadata()
                    last_fsync = monotonic_now

                if self._finalize_requested.is_set() and self._queue.empty():
                    self._finalize(handles)
                    break
                if self._stop.is_set() and self._queue.empty():
                    break
        except Exception as exc:
            with self._state_lock:
                self._writer_error = f"{type(exc).__name__}: {exc}"
                self.metadata.setdefault("stream", {})["writerError"] = self._writer_error
                self.metadata["status"] = "recorder_error"
            try:
                _atomic_json(self.metadata_path, self.metadata)
            except Exception:
                pass
        finally:
            for handle in handles.values():
                try:
                    handle.flush()
                    os.fsync(handle.fileno())
                    handle.close()
                except Exception:
                    pass
            self._write_metadata()

    def _process_trade(self, envelope: dict[str, Any], handles: dict[Path, Any]) -> None:
        raw = envelope.get("raw") if isinstance(envelope.get("raw"), dict) else {}
        exchange_ms = int(raw.get("time") or envelope.get("exchangeTimestampMs") or 0)
        receive_ns = int(envelope.get("localReceiveTimestampNs") or _now_ns())
        price = _finite(raw.get("px"))
        size = max(0.0, _finite(raw.get("sz")))
        side = str(raw.get("side") or "")
        self._append(self.trades_path, envelope, handles)
        if exchange_ms <= 0 or price <= 0:
            return
        self.observe_price(exchange_ms, price)
        self._trade_window.append((receive_ns, exchange_ms, side, price, size))
        cutoff_ns = receive_ns - int((self.settings.baseline_seconds + 2) * 1_000_000_000)
        while self._trade_window and self._trade_window[0][0] < cutoff_ns:
            self._trade_window.popleft()
        if side == "B":
            self._cvd += size
        elif side == "A":
            self._cvd -= size
        key = self._footprint_key(price)
        cell = self._footprint.setdefault(key, {"buyVolume": 0.0, "sellVolume": 0.0, "trades": 0.0})
        if side == "B":
            cell["buyVolume"] += size
        elif side == "A":
            cell["sellVolume"] += size
        cell["trades"] += 1

    def _footprint_key(self, price: float) -> float:
        step = self.settings.footprint_price_step
        return round(price / step) * step if step > 0 else price

    def _process_book(self, envelope: dict[str, Any], handles: dict[Path, Any]) -> None:
        raw = envelope.get("raw") if isinstance(envelope.get("raw"), dict) else {}
        receive_ns = int(envelope.get("localReceiveTimestampNs") or _now_ns())
        exchange_ms = int(raw.get("time") or envelope.get("exchangeTimestampMs") or 0)
        levels = raw.get("levels") or [[], []]
        bids = self._parse_levels(levels[0] if len(levels) > 0 else [])
        asks = self._parse_levels(levels[1] if len(levels) > 1 else [])
        self._append(self.book_path, envelope, handles)
        if bids and asks:
            self.observe_price(exchange_ms, (max(bids) + min(asks)) / 2.0)
        self._book_change_metrics = self._book_changes(self._last_book, {"B": bids, "A": asks}, receive_ns)
        self._last_book = {"B": bids, "A": asks}

    def _parse_levels(self, rows: list[Any]) -> dict[float, tuple[float, int]]:
        output: dict[float, tuple[float, int]] = {}
        for row in rows[: self.settings.l2_depth]:
            if not isinstance(row, dict):
                continue
            price = _finite(row.get("px"))
            if price > 0:
                output[price] = (max(0.0, _finite(row.get("sz"))), int(row.get("n") or 0))
        return output

    def _book_changes(
        self,
        previous: dict[str, dict[float, tuple[float, int]]],
        current: dict[str, dict[float, tuple[float, int]]],
        now_ns: int,
    ) -> dict[str, float]:
        metrics: dict[str, float] = {}
        for side in ("B", "A"):
            added = removed = replenished = repeated = 0.0
            for price in set(previous.get(side, {})) | set(current.get(side, {})):
                old = previous.get(side, {}).get(price, (0.0, 0))[0]
                new = current.get(side, {}).get(price, (0.0, 0))[0]
                change = new - old
                key = (side, price)
                if change > 0:
                    added += change
                    removed_at = self._last_removed.get(key)
                    if removed_at and now_ns - removed_at <= 5_000_000_000:
                        replenished += change
                        self._replenishment_count[key] = self._replenishment_count.get(key, 0) + 1
                        if self._replenishment_count[key] >= 2:
                            repeated += change
                elif change < 0:
                    removed += -change
                    self._last_removed[key] = now_ns
            prefix = "bid" if side == "B" else "ask"
            metrics[f"{prefix}LiquidityAdded"] = added
            metrics[f"{prefix}LiquidityRemoved"] = removed
            metrics[f"{prefix}LiquidityReplenished"] = replenished
            metrics[f"{prefix}RepeatedReplenishment"] = repeated
        return metrics

    def _maybe_feature(self, now_ns: int, handles: dict[Path, Any]) -> None:
        interval_ns = self.settings.feature_interval_ms * 1_000_000
        if self._last_feature_ns and now_ns - self._last_feature_ns < interval_ns:
            return
        elapsed_seconds = (
            max(1e-6, (now_ns - self._last_feature_ns) / 1_000_000_000)
            if self._last_feature_ns
            else self.settings.feature_interval_ms / 1000.0
        )
        self._last_feature_ns = now_ns
        baseline_cutoff = now_ns - self.settings.baseline_seconds * 1_000_000_000
        recent = [row for row in self._trade_window if row[0] >= baseline_cutoff]
        if now_ns - self._large_threshold_updated_ns >= 1_000_000_000:
            self._large_threshold = _percentile([row[4] for row in recent], self.settings.large_trade_quantile)
            self._large_threshold_updated_ns = now_ns
        baseline_volume = sum(row[4] for row in recent)
        baseline_rate = baseline_volume / max(1.0, float(self.settings.baseline_seconds))
        feature: dict[str, Any] = {
            "schemaVersion": SCHEMA_VERSION,
            "campaignId": self.campaign_id,
            "symbol": self.coin,
            "localTimestampNs": now_ns,
            "localTimestampMs": now_ns // 1_000_000,
            "cvd": self._cvd,
            "largeTradeThreshold": self._large_threshold,
            "largeTradeQuantile": self.settings.large_trade_quantile,
            "baselineSeconds": self.settings.baseline_seconds,
            "book": self._book_features(),
            "liquidity": dict(self._book_change_metrics),
            "windows": {},
        }
        for window_ms in self.settings.windows_ms:
            cutoff = now_ns - window_ms * 1_000_000
            rows = [row for row in recent if row[0] >= cutoff]
            buys = [row for row in rows if row[2] == "B"]
            sells = [row for row in rows if row[2] == "A"]
            buy_volume = sum(row[4] for row in buys)
            sell_volume = sum(row[4] for row in sells)
            total_volume = buy_volume + sell_volume
            delta = buy_volume - sell_volume
            seconds = max(window_ms / 1000.0, 0.001)
            sizes = [row[4] for row in rows]
            large = [row for row in rows if self._large_threshold > 0 and row[4] >= self._large_threshold]
            previous_delta = self._previous_delta.get(window_ms, delta)
            delta_change = delta - previous_delta
            self._previous_delta[window_ms] = delta
            feature["windows"][str(window_ms)] = {
                "buyVolume": buy_volume,
                "sellVolume": sell_volume,
                "totalVolume": total_volume,
                "delta": delta,
                "deltaChange": delta_change,
                "deltaVelocityPerSec": delta_change / elapsed_seconds,
                "tradeCount": len(rows),
                "tradesPerSec": len(rows) / seconds,
                "volumePerSec": total_volume / seconds,
                "buyTradesPerSec": len(buys) / seconds,
                "sellTradesPerSec": len(sells) / seconds,
                "averageTradeSize": statistics.fmean(sizes) if sizes else 0.0,
                "medianTradeSize": statistics.median(sizes) if sizes else 0.0,
                "maxTradeSize": max(sizes) if sizes else 0.0,
                "largeTradeCount": len(large),
                "largeBuyVolume": sum(row[4] for row in large if row[2] == "B"),
                "largeSellVolume": sum(row[4] for row in large if row[2] == "A"),
                "volumeSpikeRatio": total_volume / max(1e-12, baseline_rate * seconds) if baseline_rate > 0 else 0.0,
            }
        self._append(self.features_path, feature, handles)

    def _book_features(self) -> dict[str, Any]:
        bids = self._last_book.get("B", {})
        asks = self._last_book.get("A", {})
        if not bids or not asks:
            return {}
        bid_prices = sorted(bids, reverse=True)
        ask_prices = sorted(asks)
        best_bid = bid_prices[0]
        best_ask = ask_prices[0]
        mid = (best_bid + best_ask) / 2.0
        output: dict[str, Any] = {
            "bestBid": best_bid,
            "bestAsk": best_ask,
            "mid": mid,
            "spread": best_ask - best_bid,
            "byLevels": {},
            "byBps": {},
        }
        for count in sorted({1, 3, 5, 10, self.settings.l2_depth}):
            bid_liquidity = sum(bids[price][0] for price in bid_prices[:count])
            ask_liquidity = sum(asks[price][0] for price in ask_prices[:count])
            total = bid_liquidity + ask_liquidity
            output["byLevels"][str(count)] = {
                "bidLiquidity": bid_liquidity,
                "askLiquidity": ask_liquidity,
                "imbalance": bid_liquidity / total if total > 0 else 0.5,
            }
        for bps in self.settings.book_bps:
            fraction = bps / 10_000.0
            bid_floor = mid * (1.0 - fraction)
            ask_ceiling = mid * (1.0 + fraction)
            bid_liquidity = sum(size for price, (size, _n) in bids.items() if price >= bid_floor)
            ask_liquidity = sum(size for price, (size, _n) in asks.items() if price <= ask_ceiling)
            total = bid_liquidity + ask_liquidity
            output["byBps"][str(bps)] = {
                "bidLiquidity": bid_liquidity,
                "askLiquidity": ask_liquidity,
                "imbalance": bid_liquidity / total if total > 0 else 0.5,
            }
        return output

    def _write_metadata(self) -> None:
        try:
            with self._state_lock:
                self.metadata.setdefault("stream", {})["droppedQueueMessages"] = self._dropped
                self.metadata.setdefault("stream", {})["writerError"] = self._writer_error
                self.metadata["updatedAt"] = _iso_now()
                payload = deepcopy(self.metadata)
            _atomic_json(self.metadata_path, payload)
        except Exception as exc:
            with self._state_lock:
                self._writer_error = f"{type(exc).__name__}: {exc}"

    def _finalize(self, handles: dict[Path, Any]) -> None:
        for handle in handles.values():
            try:
                handle.flush()
                os.fsync(handle.fileno())
            except OSError:
                pass
        self._write_footprint()
        with self._state_lock:
            if self.metadata.get("status") != "recorder_error":
                self.metadata["status"] = "completed"
            self.metadata["finalizedAt"] = _iso_now()
        self._write_metadata()
        self._write_manifest()

    def _write_footprint(self) -> None:
        prices = sorted(self._footprint)
        rows: list[dict[str, Any]] = []
        for index, price in enumerate(prices):
            cell = self._footprint[price]
            buy = cell.get("buyVolume", 0.0)
            sell = cell.get("sellVolume", 0.0)
            previous_sell = self._footprint[prices[index - 1]].get("sellVolume", 0.0) if index > 0 else 0.0
            buy_diagonal = buy / max(previous_sell, 1e-12) if index > 0 else None
            rows.append(
                {
                    "price": price,
                    "aggressiveBuyVolume": buy,
                    "aggressiveSellVolume": sell,
                    "delta": buy - sell,
                    "totalVolume": buy + sell,
                    "tradeCount": int(cell.get("trades", 0)),
                    "buyDiagonalRatio": buy_diagonal,
                    "buyDiagonalImbalance": bool(
                        buy_diagonal is not None
                        and previous_sell > 0
                        and buy_diagonal >= self.settings.imbalance_ratio
                    ),
                }
            )
        for index, row in enumerate(rows[:-1]):
            next_buy = rows[index + 1]["aggressiveBuyVolume"]
            sell = row["aggressiveSellVolume"]
            ratio = sell / max(next_buy, 1e-12) if next_buy > 0 else None
            row["sellDiagonalRatio"] = ratio
            row["sellDiagonalImbalance"] = bool(ratio is not None and ratio >= self.settings.imbalance_ratio)
        if rows:
            rows[-1]["sellDiagonalRatio"] = None
            rows[-1]["sellDiagonalImbalance"] = False
        stacked: list[dict[str, Any]] = []
        for field, side in (("buyDiagonalImbalance", "buy"), ("sellDiagonalImbalance", "sell")):
            run: list[dict[str, Any]] = []
            for row in rows:
                if row.get(field):
                    run.append(row)
                else:
                    if len(run) >= self.settings.stacked_levels:
                        stacked.append({"side": side, "fromPrice": run[0]["price"], "toPrice": run[-1]["price"], "levels": len(run)})
                    run = []
            if len(run) >= self.settings.stacked_levels:
                stacked.append({"side": side, "fromPrice": run[0]["price"], "toPrice": run[-1]["price"], "levels": len(run)})
        _atomic_json(
            self.footprint_path,
            {
                "schemaVersion": SCHEMA_VERSION,
                "campaignId": self.campaign_id,
                "symbol": self.coin,
                "priceAggregationStep": self.settings.footprint_price_step,
                "imbalanceRatioThreshold": self.settings.imbalance_ratio,
                "stackedLevelsThreshold": self.settings.stacked_levels,
                "levels": rows,
                "stackedImbalances": stacked,
            },
        )

    def _write_manifest(self) -> None:
        files: list[dict[str, Any]] = []
        for path in (
            self.metadata_path,
            self.events_path,
            self.trades_path,
            self.book_path,
            self.features_path,
            self.footprint_path,
        ):
            if not path.is_file() or path.is_symlink():
                continue
            digest = hashlib.sha256()
            try:
                with path.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
                checksum = digest.hexdigest()
            except OSError:
                checksum = None
            files.append(
                {
                    "name": path.name,
                    "bytes": path.stat().st_size,
                    "sha256": checksum,
                    "gitSync": path.name in {"metadata.json", "events.jsonl", "footprint.json"},
                }
            )
        _atomic_json(
            self.manifest_path,
            {
                "schemaVersion": SCHEMA_VERSION,
                "campaignId": self.campaign_id,
                "symbol": self.coin,
                "finalizedAt": _iso_now(),
                "storagePolicy": {
                    "raw": "local-only by default because L2 snapshots make Git history very large",
                    "github": "compact metadata/events/footprint/manifest only",
                    "parquet": "materialize from raw JSONL later on a host with a Parquet writer",
                },
                "files": files,
            },
        )


class HyperliquidResearchStream:
    """Dedicated public-market WebSocket isolated from the trading SDK socket."""

    def __init__(
        self,
        config: Any,
        callback: Callable[[str, Any, int], None],
        status_callback: Callable[[str, dict[str, Any]], None],
    ):
        self.callback = callback
        self.status_callback = status_callback
        self.url = (
            "wss://api.hyperliquid.xyz/ws"
            if bool(getattr(config, "mainnet", True))
            else "wss://api.hyperliquid-testnet.xyz/ws"
        )
        self._desired: set[tuple[str, str]] = set()
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="galka-research-market-ws", daemon=True)
        self._started = False

    def start(self) -> None:
        if not self._started and websocket is not None:
            self._started = True
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=3.0)

    def set_coin_mode(self, coin: str, mode: str) -> None:
        normalized = str(coin).upper()
        with self._lock:
            self._desired = {row for row in self._desired if row[1] != normalized}
            if mode in {"armed", "full", "outcome"}:
                self._desired.add(("bbo", normalized))
            if mode == "full":
                self._desired.add(("trades", normalized))
                self._desired.add(("l2Book", normalized))

    def _desired_snapshot(self) -> set[tuple[str, str]]:
        with self._lock:
            return set(self._desired)

    @staticmethod
    def _send_subscription(connection: Any, method: str, item: tuple[str, str]) -> None:
        kind, coin = item
        connection.send(json.dumps({"method": method, "subscription": {"type": kind, "coin": coin}}))

    def _run(self) -> None:
        backoff = 0.5
        disconnected_at = 0
        while not self._stop.is_set():
            connection = None
            try:
                connection = websocket.create_connection(self.url, timeout=5, enable_multithread=False)
                connection.settimeout(1.0)
                subscribed: set[tuple[str, str]] = set()
                connected_ms = _now_ms()
                self.status_callback(
                    "connected",
                    {"connectedAtMs": connected_ms, "gapStartedAtMs": disconnected_at or None},
                )
                disconnected_at = 0
                backoff = 0.5
                last_ping = time.monotonic()
                while not self._stop.is_set():
                    desired = self._desired_snapshot()
                    for item in sorted(subscribed - desired):
                        self._send_subscription(connection, "unsubscribe", item)
                        subscribed.discard(item)
                    for item in sorted(desired - subscribed):
                        self._send_subscription(connection, "subscribe", item)
                        subscribed.add(item)
                    if time.monotonic() - last_ping >= 25.0:
                        connection.send(json.dumps({"method": "ping"}))
                        last_ping = time.monotonic()
                    try:
                        raw_message = connection.recv()
                    except websocket.WebSocketTimeoutException:
                        continue
                    receive_ns = _now_ns()
                    if raw_message is None:
                        raise RuntimeError("research websocket closed")
                    if isinstance(raw_message, bytes):
                        raw_message = raw_message.decode("utf-8")
                    message = json.loads(raw_message)
                    channel = str(message.get("channel") or "")
                    if channel in {"subscriptionResponse", "pong"}:
                        continue
                    if channel in {"bbo", "trades", "l2Book"}:
                        self.callback(channel, message.get("data"), receive_ns)
            except Exception as exc:
                disconnected_at = disconnected_at or _now_ms()
                self.status_callback(
                    "disconnected",
                    {"startedAtMs": disconnected_at, "message": f"{type(exc).__name__}: {exc}"},
                )
                if self._stop.wait(backoff):
                    break
                backoff = min(10.0, backoff * 1.8)
            finally:
                if connection is not None:
                    try:
                        connection.close()
                    except Exception:
                        pass


class GalkaResearchRecorder:
    """Opt-in research manager; it has no exchange mutation methods."""

    def __init__(self, config: Any):
        self.settings = RecorderSettings.from_config(config)
        self.enabled = self.settings.enabled
        self._lock = threading.RLock()
        self._sessions: dict[str, ResearchSession] = {}
        self._coin_to_campaign: dict[str, str] = {}
        self._started = False
        self._stream = HyperliquidResearchStream(config, self._on_market_message, self._on_stream_status)

    def start(self) -> None:
        if self.enabled and not self._started:
            self._started = True
            self._stream.start()

    def stop(self) -> None:
        if not self.enabled:
            return
        self._stream.stop()
        with self._lock:
            sessions = list(self._sessions.values())
        for session in sessions:
            session.shutdown_flush()

    def arm_campaign(self, campaign: dict[str, Any]) -> None:
        if not self.enabled:
            return
        campaign_id = str(campaign.get("id") or "")
        coin = str(campaign.get("coin") or "").upper()
        if not campaign_id or not coin:
            return
        with self._lock:
            session = self._sessions.get(campaign_id)
            if session is None:
                session = ResearchSession(self.settings, campaign)
                self._sessions[campaign_id] = session
                self._coin_to_campaign[coin] = campaign_id
                session.start()
            else:
                session.update_campaign(campaign, "restored_from_state")
            mode = "full" if session.recording and not session.campaign_completed_ms else "armed"
        self._stream.set_coin_mode(coin, mode)

    def restore_active_campaigns(self, campaigns: list[dict[str, Any]]) -> None:
        if not self.enabled:
            return
        for campaign in campaigns:
            self.arm_campaign(campaign)
            session = self._session_for_campaign(str(campaign.get("id") or ""))
            if session:
                session.record_galka_event(
                    "research",
                    "recorder restored after process start",
                    {
                        "campaignId": session.campaign_id,
                        "resumeAtMs": _now_ms(),
                        "recordingWasActive": session.recording,
                    },
                )

    def _session_for_campaign(self, campaign_id: str) -> ResearchSession | None:
        with self._lock:
            return self._sessions.get(str(campaign_id))

    def _session_for_coin(self, coin: str) -> ResearchSession | None:
        with self._lock:
            campaign_id = self._coin_to_campaign.get(str(coin).upper())
            return self._sessions.get(campaign_id) if campaign_id else None

    def on_campaign_snapshot(self, campaign: dict[str, Any], reason: str) -> None:
        if not self.enabled:
            return
        campaign_id = str(campaign.get("id") or "")
        session = self._session_for_campaign(campaign_id)
        if session is None and campaign.get("status") in ACTIVE_CAMPAIGN_STATUSES:
            self.arm_campaign(campaign)
            session = self._session_for_campaign(campaign_id)
        if session is None:
            return
        session.update_campaign(campaign, reason)
        if campaign.get("completedAt") or campaign.get("status") not in ACTIVE_CAMPAIGN_STATUSES:
            self._complete_session(session, campaign)

    def on_galka_event(
        self,
        event_type: str,
        message: str,
        meta: dict[str, Any],
        campaign: dict[str, Any] | None,
    ) -> None:
        if not self.enabled or not campaign:
            return
        campaign_id = str(campaign.get("id") or meta.get("campaignId") or "")
        session = self._session_for_campaign(campaign_id)
        if session is None and campaign.get("status") in ACTIVE_CAMPAIGN_STATUSES:
            self.arm_campaign(campaign)
            session = self._session_for_campaign(campaign_id)
        if session is None:
            return
        session.record_galka_event(event_type, message, meta)
        session.update_campaign(campaign, f"event:{event_type}")
        lowered = message.lower()
        if (
            session.crossed_below_ms
            and not session.return_to_galka_ms
            and ("owned tp" in lowered or "на galka" in lowered)
            and ("закры" in lowered or "заверш" in lowered)
        ):
            event_ms = _parse_iso_ms(campaign.get("completedAt")) or _now_ms()
            session.mark_return_to_galka(event_ms, _now_ns(), session.galka, "galka_owned_exit_event")
        if campaign.get("completedAt") or campaign.get("status") not in ACTIVE_CAMPAIGN_STATUSES:
            self._complete_session(session, campaign)

    def on_execution_fill(self, campaign: dict[str, Any], fill: dict[str, Any]) -> None:
        if not self.enabled:
            return
        session = self._session_for_campaign(str(campaign.get("id") or ""))
        if session:
            session.record_execution(fill)

    def _complete_session(self, session: ResearchSession, campaign: dict[str, Any]) -> None:
        session.complete_campaign(campaign)
        if session.post_outcome_pending():
            self._stream.set_coin_mode(session.coin, "outcome")
        else:
            self._stream.set_coin_mode(session.coin, "off")
            session.request_finalize()

    def _on_stream_status(self, status: str, meta: dict[str, Any]) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
        if status == "disconnected":
            for session in sessions:
                session.record_stream_gap(
                    str(meta.get("message") or "websocket disconnected"),
                    meta.get("startedAtMs"),
                    None,
                )
        elif status == "connected" and meta.get("gapStartedAtMs"):
            for session in sessions:
                session.record_stream_gap(
                    "websocket reconnected",
                    meta.get("gapStartedAtMs"),
                    meta.get("connectedAtMs"),
                )

    def _on_market_message(self, channel: str, data: Any, receive_ns: int) -> None:
        if channel == "trades":
            for row in data if isinstance(data, list) else []:
                if not isinstance(row, dict):
                    continue
                coin = str(row.get("coin") or "").upper()
                session = self._session_for_coin(coin)
                if not session or not session.recording or session.campaign_completed_ms:
                    continue
                exchange_ms = int(row.get("time") or 0)
                session.enqueue(
                    "trade",
                    {
                        "schemaVersion": SCHEMA_VERSION,
                        "campaignId": session.campaign_id,
                        "symbol": coin,
                        "exchangeTimestampMs": exchange_ms,
                        "localReceiveTimestampNs": receive_ns,
                        "localReceiveTimestampMs": receive_ns // 1_000_000,
                        "latencyMs": (receive_ns // 1_000_000 - exchange_ms) if exchange_ms else None,
                        "aggressorSide": row.get("side"),
                        "raw": deepcopy(row),
                    },
                )
            return
        if not isinstance(data, dict):
            return
        coin = str(data.get("coin") or "").upper()
        session = self._session_for_coin(coin)
        if not session:
            return
        exchange_ms = int(data.get("time") or 0)
        if channel == "bbo":
            bbo = data.get("bbo") or [None, None]
            bid = _finite((bbo[0] or {}).get("px")) if len(bbo) > 0 and isinstance(bbo[0], dict) else 0.0
            ask = _finite((bbo[1] or {}).get("px")) if len(bbo) > 1 and isinstance(bbo[1], dict) else 0.0
            price = (bid + ask) / 2.0 if bid > 0 and ask > 0 else (bid or ask)
            if price <= 0:
                return
            event_ms = exchange_ms or receive_ns // 1_000_000
            session.observe_price(event_ms, price)
            if not session.crossed_below_ms and price < session.galka:
                if session.mark_crossed_below(exchange_ms, receive_ns, price, "bbo_mid"):
                    self._stream.set_coin_mode(coin, "full")
            elif session.crossed_below_ms and not session.return_to_galka_ms and price >= session.galka:
                session.mark_return_to_galka(exchange_ms, receive_ns, price, "bbo_mid")
            if session.campaign_completed_ms and not session.post_outcome_pending(event_ms):
                self._stream.set_coin_mode(coin, "off")
                session.request_finalize()
            return
        if channel == "l2Book" and session.recording and not session.campaign_completed_ms:
            levels = data.get("levels") or [[], []]
            trimmed = deepcopy(data)
            trimmed["levels"] = [
                list((levels[0] if len(levels) > 0 else [])[: self.settings.l2_depth]),
                list((levels[1] if len(levels) > 1 else [])[: self.settings.l2_depth]),
            ]
            session.enqueue(
                "book",
                {
                    "schemaVersion": SCHEMA_VERSION,
                    "campaignId": session.campaign_id,
                    "symbol": coin,
                    "exchangeTimestampMs": exchange_ms,
                    "localReceiveTimestampNs": receive_ns,
                    "localReceiveTimestampMs": receive_ns // 1_000_000,
                    "latencyMs": (receive_ns // 1_000_000 - exchange_ms) if exchange_ms else None,
                    "raw": trimmed,
                },
            )

    def status(self) -> dict[str, Any]:
        with self._lock:
            sessions = list(self._sessions.values())
        return {
            "enabled": self.enabled,
            "root": str(self.settings.root),
            "activeSessions": [
                {
                    "campaignId": session.campaign_id,
                    "symbol": session.coin,
                    "recording": session.recording,
                    "crossedBelowGalkaMs": session.crossed_below_ms or None,
                    "returnToGalkaMs": session.return_to_galka_ms or None,
                    "campaignCompletedMs": session.campaign_completed_ms or None,
                    "postReturnPending": session.post_outcome_pending(),
                }
                for session in sessions
                if not session.finalized
            ],
        }

"""Compatibility wrappers with latency diagnostics.

Trading behavior stays in the hardened base gateway and engine. This module only
records monotonic timings for placement/cancellation and avoids repeating an
already-confirmed leverage write within the same server process.
"""

from __future__ import annotations

import threading
import time
from copy import deepcopy
from typing import Any, Callable, TypeVar

from .engine import GalkaLiveEngine, now_iso
from .hyperliquid_gateway import HyperliquidGateway

T = TypeVar("T")


class CompatibleHyperliquidGateway(HyperliquidGateway):
    """Hardened gateway plus timing collection and conservative leverage caching."""

    def __init__(self, config: Any):
        super().__init__(config)
        self._trace_lock = threading.RLock()
        self._trace_name: str | None = None
        self._trace_started = 0.0
        self._trace_rows: list[dict[str, Any]] = []
        self._confirmed_leverage: set[str] = set()
        self._leverage_lock = threading.RLock()

    def begin_trace(self, name: str) -> None:
        with self._trace_lock:
            self._trace_name = name
            self._trace_started = time.monotonic()
            self._trace_rows = []

    def finish_trace(self) -> dict[str, Any]:
        with self._trace_lock:
            total_ms = round((time.monotonic() - self._trace_started) * 1000, 1) if self._trace_started else 0.0
            result = {
                "name": self._trace_name,
                "totalMs": total_ms,
                "stages": deepcopy(self._trace_rows),
            }
            self._trace_name = None
            self._trace_started = 0.0
            self._trace_rows = []
            return result

    def _timed(self, label: str, action: Callable[[], T]) -> T:
        started = time.monotonic()
        ok = False
        try:
            result = action()
            ok = True
            return result
        finally:
            elapsed_ms = round((time.monotonic() - started) * 1000, 1)
            with self._trace_lock:
                if self._trace_name is not None:
                    self._trace_rows.append({"stage": label, "ms": elapsed_ms, "ok": ok})

    def set_leverage(self, coin: str) -> dict[str, Any]:
        normalized = self._coin(coin)
        with self._leverage_lock:
            if normalized in self._confirmed_leverage:
                return self._timed(
                    "set_leverage_cached",
                    lambda: {
                        "status": "ok",
                        "response": {"type": "default", "data": {"cached": True}},
                    },
                )
            response = self._timed(
                "set_leverage",
                lambda: super(CompatibleHyperliquidGateway, self).set_leverage(normalized),
            )
            # Cache only after Hyperliquid has accepted the write. A server restart
            # intentionally clears this memory and confirms leverage again.
            self._confirmed_leverage.add(normalized)
            return response

    def place_entry_with_target(
        self,
        coin: str,
        level: Any,
        galka_price: float,
        entry_cloid: str | None = None,
        target_cloid: str | None = None,
    ) -> Any:
        index = int(getattr(level, "index", 0) or 0)
        return self._timed(
            f"place_L{index}_with_TP",
            lambda: super(CompatibleHyperliquidGateway, self).place_entry_with_target(
                coin, level, galka_price, entry_cloid, target_cloid
            ),
        )

    def cancel_oids(self, coin: str, oids: list[int]) -> dict[str, Any]:
        return self._timed(
            f"bulk_cancel_{len(oids)}",
            lambda: super(CompatibleHyperliquidGateway, self).cancel_oids(coin, oids),
        )

    def fresh_account_state(self) -> dict[str, Any]:
        return self._timed(
            "fresh_account_state",
            lambda: super(CompatibleHyperliquidGateway, self).fresh_account_state(),
        )

    def fresh_open_orders(self, coin: str | None = None) -> list[dict[str, Any]]:
        suffix = coin or "all"
        return self._timed(
            f"fresh_open_orders_{suffix}",
            lambda: super(CompatibleHyperliquidGateway, self).fresh_open_orders(coin),
        )

    def fills_since(self, start_ms: int) -> list[dict[str, Any]]:
        return self._timed(
            "fills_since",
            lambda: super(CompatibleHyperliquidGateway, self).fills_since(start_ms),
        )


class CompatibleGalkaLiveEngine(GalkaLiveEngine):
    """Base hardened engine with user-visible latency reports in the event log."""

    def _record_latency(self, operation: str, coin: str, trace: dict[str, Any], success: bool) -> None:
        stages = trace.get("stages") or []
        slowest = sorted(stages, key=lambda row: float(row.get("ms") or 0), reverse=True)[:5]
        summary = " · ".join(
            f"{row.get('stage')} {float(row.get('ms') or 0):.0f} ms" for row in slowest
        ) or "нет сетевых этапов"
        with self.lock:
            self._event_locked(
                "latency" if success else "error",
                f"{coin}: {operation} {'завершено' if success else 'ошибка'} за {float(trace.get('totalMs') or 0):.0f} ms; {summary}",
                operation=operation,
                coin=coin,
                success=success,
                totalMs=trace.get("totalMs"),
                stages=stages,
            )
            self.state.setdefault("system", {})["lastLatency"] = {
                "time": now_iso(),
                "operation": operation,
                "coin": coin,
                "success": success,
                **trace,
            }
            self._save_locked()

    def create_campaign(self, coin: str, galka_price: float, confirmation: str) -> dict[str, Any]:
        normalized = self._coin(coin)
        gateway = self.gateway
        gateway.begin_trace("create_campaign")
        success = False
        try:
            result = super().create_campaign(normalized, galka_price, confirmation)
            success = True
            return result
        finally:
            self._record_latency("выставление GALKA", normalized, gateway.finish_trace(), success)

    def cancel_waiting_campaign(self, coin: str) -> dict[str, Any]:
        normalized = self._coin(coin)
        gateway = self.gateway
        gateway.begin_trace("cancel_campaign")
        success = False
        try:
            result = super().cancel_waiting_campaign(normalized)
            success = True
            return result
        finally:
            self._record_latency("отмена GALKA", normalized, gateway.finish_trace(), success)

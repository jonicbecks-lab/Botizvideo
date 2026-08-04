"""Compatibility wrappers with latency diagnostics and manual near-market exit."""

from __future__ import annotations

import threading
import time
from copy import deepcopy
from typing import Any, Callable, TypeVar

from .engine import GalkaLiveEngine, LiveEngineError, new_cloid, now_iso
from .hyperliquid_gateway import GatewayError, HyperliquidGateway
from .live_ladder import round_perp_price, round_size_down

T = TypeVar("T")


class CompatibleHyperliquidGateway(HyperliquidGateway):
    """Hardened gateway plus diagnostics and conservative leverage caching."""

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
            result = {"name": self._trace_name, "totalMs": total_ms, "stages": deepcopy(self._trace_rows)}
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
                    lambda: {"status": "ok", "response": {"type": "default", "data": {"cached": True}}},
                )
            response = self._timed(
                "set_leverage",
                lambda: super(CompatibleHyperliquidGateway, self).set_leverage(normalized),
            )
            self._confirmed_leverage.add(normalized)
            return response

    def place_entry_with_target(self, coin: str, level: Any, galka_price: float, entry_cloid: str | None = None, target_cloid: str | None = None) -> Any:
        index = int(getattr(level, "index", 0) or 0)
        return self._timed(
            f"place_L{index}_with_TP",
            lambda: super(CompatibleHyperliquidGateway, self).place_entry_with_target(
                coin, level, galka_price, entry_cloid, target_cloid
            ),
        )

    def place_post_only_reduce_sell(self, coin: str, quantity: float, price: float, cloid: str) -> Any:
        """Place one maker-only reduce-only sell. It can never open a short."""
        self._require_live_write("place near-market reduce-only exit")
        normalized = self._coin(coin)
        size = round_size_down(abs(float(quantity)), self.sz_decimals(normalized))
        if size <= 0:
            raise GatewayError("Near-market exit size rounded to zero")
        limit_price = round_perp_price(float(price), self.sz_decimals(normalized))
        with self._io_lock:
            response = self.exchange.order(
                normalized,
                False,
                size,
                limit_price,
                {"limit": {"tif": "Alo"}},
                reduce_only=True,
                cloid=self._cloid(cloid),
            )
        self._invalidate("open_orders", "account_state")
        rows = self._parse_order_response(response, [None], [cloid])
        if len(rows) != 1 or rows[0].status != "resting" or rows[0].oid <= 0:
            raise GatewayError("Near-market post-only exit was not accepted as a resting order")
        return rows[0]

    def cancel_oids(self, coin: str, oids: list[int]) -> dict[str, Any]:
        return self._timed(
            f"bulk_cancel_{len(oids)}",
            lambda: super(CompatibleHyperliquidGateway, self).cancel_oids(coin, oids),
        )

    def fresh_account_state(self) -> dict[str, Any]:
        return self._timed("fresh_account_state", lambda: super(CompatibleHyperliquidGateway, self).fresh_account_state())

    def fresh_open_orders(self, coin: str | None = None) -> list[dict[str, Any]]:
        suffix = coin or "all"
        return self._timed(
            f"fresh_open_orders_{suffix}",
            lambda: super(CompatibleHyperliquidGateway, self).fresh_open_orders(coin),
        )

    def fills_since(self, start_ms: int) -> list[dict[str, Any]]:
        return self._timed("fills_since", lambda: super(CompatibleHyperliquidGateway, self).fills_since(start_ms))


class CompatibleGalkaLiveEngine(GalkaLiveEngine):
    """Base hardened engine with latency reports and a controlled manual exit."""

    _NEAR_MARKET_STEPS = {"BTC": 1.0, "ETH": 0.10, "SOL": 0.01}

    def _record_latency(self, operation: str, coin: str, trace: dict[str, Any], success: bool) -> None:
        stages = trace.get("stages") or []
        slowest = sorted(stages, key=lambda row: float(row.get("ms") or 0), reverse=True)[:5]
        summary = " · ".join(f"{row.get('stage')} {float(row.get('ms') or 0):.0f} ms" for row in slowest) or "нет сетевых этапов"
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
                "time": now_iso(), "operation": operation, "coin": coin, "success": success, **trace
            }
            self._save_locked()

    def create_campaign(self, coin: str, galka_price: float, confirmation: str) -> dict[str, Any]:
        normalized = self._coin(coin)
        self.gateway.begin_trace("create_campaign")
        success = False
        try:
            result = super().create_campaign(normalized, galka_price, confirmation)
            success = True
            return result
        finally:
            self._record_latency("выставление GALKA", normalized, self.gateway.finish_trace(), success)

    def cancel_waiting_campaign(self, coin: str) -> dict[str, Any]:
        normalized = self._coin(coin)
        self.gateway.begin_trace("cancel_campaign")
        success = False
        try:
            result = super().cancel_waiting_campaign(normalized)
            success = True
            return result
        finally:
            self._record_latency("отмена GALKA", normalized, self.gateway.finish_trace(), success)

    def close_near_market(self, coin: str, confirmation: str) -> dict[str, Any]:
        """Cancel the GALKA orders and replace its TP with one maker-only exit near market."""
        normalized = self._coin(coin)
        self._require_live_writes()
        if confirmation != "CLOSE_NEAR_MARKET":
            raise LiveEngineError("Не подтверждено закрытие рядом с рынком")

        with self.action_lock:
            with self.lock:
                campaign = self._active_campaign_locked(normalized)
                if not campaign:
                    raise LiveEngineError(f"Для {normalized} нет активной GALKA")
                campaign["autoRearmBlocked"] = True
                campaign["abortAfterClose"] = True
                campaign["status"] = "manual_exit"
                campaign["updatedAt"] = now_iso()
                self._save_locked()

            account = self.gateway.fresh_account_state()
            position_size = self._position_size(account, normalized)
            tolerance = self._size_tolerance(normalized)
            if position_size <= tolerance:
                raise LiveEngineError("Открытой long-позиции для закрытия нет")

            open_orders = self.gateway.fresh_open_orders(normalized)
            self._cancel_owned_orders(campaign, open_orders=open_orders)

            account = self.gateway.fresh_account_state()
            position_size = self._position_size(account, normalized)
            if position_size <= tolerance:
                raise LiveEngineError("Позиция уже закрылась во время подготовки выхода")

            cloid = new_cloid()
            step = self._NEAR_MARKET_STEPS[normalized]
            placed = None
            exit_price = 0.0
            last_error: Exception | None = None
            for multiplier in (1, 2, 5):
                mid = float(self.gateway.mids().get(normalized) or 0)
                if mid <= 0:
                    raise LiveEngineError(f"Нет свежей рыночной цены {normalized}")
                exit_price = round_perp_price(mid + step * multiplier, self.gateway.sz_decimals(normalized))
                if exit_price <= mid:
                    exit_price = round_perp_price(mid + step * (multiplier + 1), self.gateway.sz_decimals(normalized))
                try:
                    placed = self.gateway.place_post_only_reduce_sell(
                        normalized, position_size, exit_price, cloid
                    )
                    break
                except Exception as exc:
                    last_error = exc
                    time.sleep(0.15)
            if placed is None:
                self._enter_recovery(
                    campaign,
                    f"Не удалось выставить maker-выход рядом с рынком: {last_error}",
                    position_size,
                    self.gateway.fresh_open_orders(normalized),
                )
                raise LiveEngineError("Выход рядом с рынком не выставлен; включён recovery")

            with self.lock:
                campaign["galkaPrice"] = exit_price
                campaign["manualExitPrice"] = exit_price
                campaign["fallbackTargetOid"] = placed.oid
                campaign["fallbackTargetCloid"] = cloid
                campaign.setdefault("targetOidMap", {})[str(placed.oid)] = 0
                campaign.setdefault("targetCloidMap", {})[cloid] = 0
                campaign["status"] = "closing"
                campaign["updatedAt"] = now_iso()
                self._event_locked(
                    "live",
                    f"{normalized}: входы и старые TP сняты; весь объём выставлен на продажу по {exit_price:g}",
                    campaignId=campaign["id"],
                    price=exit_price,
                    size=position_size,
                    oid=placed.oid,
                )
                self._save_locked()
                return {
                    "coin": normalized,
                    "price": exit_price,
                    "size": position_size,
                    "oid": placed.oid,
                    "status": "closing",
                }

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
        self._trace_account_reads = 0
        self._trace_account_snapshot: dict[str, Any] | None = None
        self._trace_account_snapshot_at = 0.0
        self._confirmed_leverage: set[str] = set()
        self._leverage_lock = threading.RLock()

    def begin_trace(self, name: str) -> None:
        with self._trace_lock:
            self._trace_name = name
            self._trace_started = time.monotonic()
            self._trace_rows = []
            self._trace_account_reads = 0
            self._trace_account_snapshot = None
            self._trace_account_snapshot_at = 0.0

    def finish_trace(self) -> dict[str, Any]:
        with self._trace_lock:
            total_ms = round((time.monotonic() - self._trace_started) * 1000, 1) if self._trace_started else 0.0
            result = {"name": self._trace_name, "totalMs": total_ms, "stages": deepcopy(self._trace_rows)}
            self._trace_name = None
            self._trace_started = 0.0
            self._trace_rows = []
            self._trace_account_reads = 0
            self._trace_account_snapshot = None
            self._trace_account_snapshot_at = 0.0
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
        # create_campaign reads the account once in preview and immediately once
        # again in preflight. Reuse only that second read inside the same trace;
        # the post-order verification remains a real fresh venue read.
        with self._trace_lock:
            trace_name = self._trace_name
            read_no = self._trace_account_reads
            snapshot = self._trace_account_snapshot
            snapshot_age = time.monotonic() - self._trace_account_snapshot_at
            self._trace_account_reads += 1
        if trace_name == "create_campaign" and read_no == 1 and snapshot is not None and snapshot_age < 2.0:
            return self._timed("fresh_account_state_reused", lambda: deepcopy(snapshot))
        result = self._timed(
            "fresh_account_state",
            lambda: super(CompatibleHyperliquidGateway, self).fresh_account_state(),
        )
        if trace_name == "create_campaign" and read_no == 0:
            with self._trace_lock:
                self._trace_account_snapshot = deepcopy(result)
                self._trace_account_snapshot_at = time.monotonic()
        return result

    def fresh_open_orders(self, coin: str | None = None) -> list[dict[str, Any]]:
        suffix = coin or "all"
        return self._timed(
            f"fresh_open_orders_{suffix}",
            lambda: super(CompatibleHyperliquidGateway, self).fresh_open_orders(coin),
        )

    def fills_since(self, start_ms: int) -> list[dict[str, Any]]:
        return self._timed("fills_since", lambda: super(CompatibleHyperliquidGateway, self).fills_since(start_ms))


class CompatibleGalkaLiveEngine(GalkaLiveEngine):
    """Base hardened engine with latency reports and controlled recovery grace."""

    _NEAR_MARKET_STEPS = {"BTC": 1.0, "ETH": 0.10, "SOL": 0.01}
    _MISMATCH_CONFIRMATIONS = 3
    _MISMATCH_GRACE_SECONDS = 10.0

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

    def _campaign_snapshot_locked(self, campaign: dict[str, Any], reason: str) -> dict[str, Any]:
        levels = []
        for level in campaign.get("levels", []):
            levels.append(
                {
                    "index": level.get("index"),
                    "requestedSize": level.get("size"),
                    "filledSize": level.get("filledSize"),
                    "averageFillPrice": level.get("averageFillPrice"),
                    "status": level.get("status"),
                }
            )
        return {
            "time": now_iso(),
            "campaignId": campaign.get("id"),
            "coin": campaign.get("coin"),
            "reason": reason,
            "status": campaign.get("status"),
            "galkaPrice": campaign.get("galkaPrice"),
            "actualPositionSize": campaign.get("actualPositionSize"),
            "managedNetSize": campaign.get("managedNetSize"),
            "cycleDeepest": campaign.get("cycleDeepest"),
            "l1Cycles": campaign.get("l1Cycles"),
            "l1RealizedPnl": campaign.get("l1RealizedPnl"),
            "cycleClosedPnl": campaign.get("cycleClosedPnl"),
            "cycleFees": campaign.get("cycleFees"),
            "levels": levels,
        }

    def _append_campaign_journal_locked(self, campaign: dict[str, Any], reason: str) -> None:
        journal = self.state.setdefault("campaignJournal", [])
        journal.append(self._campaign_snapshot_locked(campaign, reason))
        del journal[:-200]

    def _clear_mismatch_candidate_locked(self, campaign: dict[str, Any]) -> None:
        campaign.pop("mismatchCandidate", None)

    def _refresh_owned_fill_state(self, campaign: dict[str, Any]) -> tuple[float, list[dict[str, Any]]]:
        coin = campaign["coin"]
        cursor = max(0, int(campaign.get("fillCursorMs") or campaign["createdMs"] - 60_000))
        fills = [row for row in self.gateway.fills_since(cursor) if row.get("coin") == coin]
        prepared = self._prepare_fill_owners(campaign, fills)
        account = self.gateway.fresh_account_state()
        open_orders = self.gateway.fresh_open_orders(coin)
        with self.lock:
            self._register_delayed_orders(campaign, open_orders)
            self._apply_new_fills(campaign, prepared)
            actual = self._position_size(account, coin)
            campaign["actualPositionSize"] = actual
            campaign["updatedAt"] = now_iso()
            self._save_locked()
        return actual, open_orders

    def _enter_recovery(
        self,
        campaign: dict[str, Any],
        reason: str,
        actual_size: float,
        open_orders: list[dict[str, Any]],
    ) -> None:
        # Position and fills are delivered by separate Hyperliquid reads. A TP can
        # close a partial level before its sell fill appears in the next fills
        # response. Treat a plain size mismatch as provisional, re-read the venue,
        # and recover only after repeated independent confirmations.
        if reason.startswith("Расхождение позиции:") and campaign.get("status") != "recovery":
            now = time.monotonic()
            with self.lock:
                candidate = campaign.get("mismatchCandidate") or {
                    "firstMonotonic": now,
                    "confirmations": 0,
                    "reason": reason,
                }
                candidate["confirmations"] = int(candidate.get("confirmations") or 0) + 1
                candidate["lastReason"] = reason
                candidate["lastActual"] = actual_size
                candidate["lastManaged"] = float(campaign.get("managedNetSize") or 0)
                campaign["mismatchCandidate"] = candidate
                self._event_locked(
                    "risk",
                    f"{campaign['coin']}: временное расхождение позиции; выполняется повторная сверка",
                    campaignId=campaign["id"],
                    actual=actual_size,
                    managed=campaign.get("managedNetSize"),
                    confirmation=candidate["confirmations"],
                )
                self._save_locked()

            try:
                refreshed_actual, refreshed_orders = self._refresh_owned_fill_state(campaign)
            except Exception as exc:
                refreshed_actual, refreshed_orders = actual_size, open_orders
                with self.lock:
                    campaign["lastError"] = f"Повторная сверка расхождения: {exc}"
                    self._save_locked()

            with self.lock:
                managed = float(campaign.get("managedNetSize") or 0)
                tolerance = self._size_tolerance(campaign["coin"])
                candidate = campaign.get("mismatchCandidate") or {}
                confirmations = int(candidate.get("confirmations") or 0)
                elapsed = now - float(candidate.get("firstMonotonic") or now)
                resolved = abs(refreshed_actual - managed) <= tolerance
                if resolved:
                    self._clear_mismatch_candidate_locked(campaign)
                    campaign["lastError"] = None
                    self._event_locked(
                        "live",
                        f"{campaign['coin']}: временное расхождение устранено повторной сверкой",
                        campaignId=campaign["id"],
                        actual=refreshed_actual,
                        managed=managed,
                    )
                    self._save_locked()
                    return
                should_recover = (
                    confirmations >= self._MISMATCH_CONFIRMATIONS
                    or elapsed >= self._MISMATCH_GRACE_SECONDS
                )
                if not should_recover:
                    campaign["lastError"] = (
                        f"Ожидание подтверждения позиции: биржа {refreshed_actual:g}, "
                        f"GALKA {managed:g}"
                    )
                    self._save_locked()
                    try:
                        if refreshed_actual > tolerance:
                            self._ensure_target_coverage(campaign, refreshed_orders, refreshed_actual)
                    except Exception as exc:
                        campaign["lastError"] = f"Временное расхождение; target: {exc}"
                        self._save_locked()
                    return
                reason = (
                    f"Устойчивое расхождение после {confirmations} сверок: "
                    f"биржа {refreshed_actual:g}, GALKA {managed:g}"
                )
                actual_size = refreshed_actual
                open_orders = refreshed_orders
                self._append_campaign_journal_locked(campaign, reason)
                self._save_locked()

        super()._enter_recovery(campaign, reason, actual_size, open_orders)

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

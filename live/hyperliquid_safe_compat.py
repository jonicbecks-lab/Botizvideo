"""Production-safe compatibility layer for Galka LIVE.

Keeps the exchange-tested pair-wise normalTpsl placement path, fixes validation
of rounded fallback targets, adds a fast two-phase cancel path for empty GALKA
campaigns, auto-sizes new campaigns from current equity, and records non-secret
research data independently from trading state.
"""

from __future__ import annotations

import math
import threading
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

from .engine import ACTIVE_STATUSES, LiveEngineError, now_iso
from .hyperliquid_compat import (
    CompatibleGalkaLiveEngine as _OptimizedEngine,
    CompatibleHyperliquidGateway as _BatchGateway,
)
from .hyperliquid_gateway import EntryWithTarget
from .live_ladder import (
    LadderLevel,
    estimated_target_pnl,
    estimated_target_pnl_mixed,
    round_perp_price,
    weighted_average,
)
from .research_journal import ResearchJournal


class SafeCompatibleHyperliquidGateway(_BatchGateway):
    """Use the exchange-tested one-pair normalTpsl submission format."""

    def place_ladder_with_targets(
        self,
        coin: str,
        levels: list[LadderLevel],
        galka_price: float,
        entry_cloids: list[str],
        target_cloids: list[str],
    ) -> list[EntryWithTarget]:
        if not levels or len(levels) != len(entry_cloids) or len(levels) != len(target_cloids):
            from .hyperliquid_gateway import GatewayError

            raise GatewayError("Incomplete ladder batch parameters")

        pairs: list[EntryWithTarget] = []
        for level, entry_cloid, target_cloid in zip(levels, entry_cloids, target_cloids):
            pairs.append(
                super().place_entry_with_target(
                    coin,
                    level,
                    galka_price,
                    entry_cloid,
                    target_cloid,
                )
            )
        return pairs


class SafeCompatibleGalkaLiveEngine(_OptimizedEngine):
    """Safe pair-wise trading plus best-effort research telemetry."""

    def __init__(self, config: Any, gateway: Any):
        repo_root = Path(__file__).resolve().parents[1]
        self.research_journal = ResearchJournal(config.data_dir, repo_root)
        # Set before the base constructor creates the monitor thread. A pending
        # manual cancel cannot interrupt an in-flight reconciliation, but it can
        # prevent the monitor from immediately starting another campaign/global
        # scan while the user is waiting for the action lock.
        self._manual_action_pending = threading.Event()
        super().__init__(config, gateway)

    def preview(self, coin: str, galka_price: float) -> dict[str, Any]:
        """Size a new GALKA from current account equity instead of a stale fixed notional.

        HL_MAX_MARGIN_FRACTION is both the aggregate margin ceiling and the target
        fraction for a fresh campaign. Existing active campaigns are reserved first,
        so the combined full-fill margin can never exceed the configured fraction.
        """
        normalized = self._coin(coin)
        price = float(galka_price)
        if not math.isfinite(price) or price <= 0:
            raise LiveEngineError("Цена GALKA должна быть конечным числом больше нуля")

        mid = float(self.gateway.mids().get(normalized) or 0)
        if mid <= 0:
            raise LiveEngineError(f"Нет текущей цены {normalized}")
        if mid <= price:
            raise LiveEngineError(
                f"Текущая цена {mid:g} уже не выше GALKA {price:g}. Сетка должна ждать падения сверху."
            )

        account = self.gateway.fresh_account_state()
        account_value = float(account.get("accountValue") or 0)
        with self.lock:
            reserved_margin = sum(
                float(active.get("actualNotional") or active.get("requestedNotional") or 0)
                / max(1, int(active.get("leverage") or self.config.leverage))
                for active in self._active_campaigns_locked()
            )

        allowed_margin = max(0.0, account_value * self.config.max_margin_fraction)
        target_margin = max(0.0, allowed_margin - reserved_margin)
        requested_notional = target_margin * self.config.leverage
        if requested_notional < 80:
            raise LiveEngineError(
                f"Недостаточно свободной маржи для новой GALKA: доступно ${target_margin:.2f} "
                f"из лимита {self.config.max_margin_fraction:.0%} капитала ${account_value:.2f}."
            )

        levels = self.gateway.preview_ladder(normalized, price, requested_notional)
        actual_notional = sum(level.notional for level in levels)
        return {
            "coin": normalized,
            "galkaPrice": price,
            "currentPrice": mid,
            "levels": [level.to_dict() for level in levels],
            "requestedNotional": requested_notional,
            "actualNotional": actual_notional,
            "requiredMargin": actual_notional / self.config.leverage,
            "leverage": self.config.leverage,
            "isolated": self.config.isolated,
            "weightedAverage": weighted_average(levels),
            "estimatedPnlAtGalka": estimated_target_pnl(
                levels, price, self.config.maker_fee_rate
            ),
            "estimatedPnlMakerMaker": estimated_target_pnl(
                levels, price, self.config.maker_fee_rate
            ),
            "estimatedPnlMakerTaker": estimated_target_pnl_mixed(
                levels,
                price,
                self.config.maker_fee_rate,
                self.config.taker_fee_rate,
            ),
            "makerFeeRate": self.config.maker_fee_rate,
            "takerFeeRate": self.config.taker_fee_rate,
            "accountValue": account_value,
            "withdrawable": account.get("withdrawable"),
            "reservedMargin": reserved_margin,
            "targetMargin": target_margin,
            "autoSizedFromEquity": True,
            "liveEnabled": self.config.live_enabled,
        }

    def _new_campaign(
        self,
        campaign_id: str,
        coin: str,
        galka_price: float,
        preview: dict[str, Any],
        levels: list[LadderLevel],
    ) -> dict[str, Any]:
        campaign = super()._new_campaign(campaign_id, coin, galka_price, preview, levels)
        campaign["requestedNotional"] = float(preview.get("requestedNotional") or 0)
        campaign["autoSizedFromEquity"] = True
        campaign["targetMarginFraction"] = self.config.max_margin_fraction
        return campaign

    def start(self) -> None:
        super().start()
        self.research_journal.start_background_sync()
        threading.Thread(
            target=self._sync_research_once,
            name="galka-research-initial-sync",
            daemon=True,
        ).start()

    def stop(self) -> None:
        self.research_journal.stop()
        super().stop()

    def _monitor_loop(self) -> None:
        """Reconcile safely without monopolizing the action lock across all work."""
        next_global_check = 0.0
        while not self.stop_event.wait(self.config.monitor_interval):
            if not self.config.live_enabled:
                continue

            with self.lock:
                self.state.setdefault("system", {})["monitorHeartbeatAt"] = now_iso()
                campaigns = [
                    campaign
                    for campaign in self.state.get("campaigns", {}).values()
                    if campaign.get("status") in ACTIVE_STATUSES
                ]

            # Keep each campaign sync atomic relative to writes, but release the
            # lock between coins. If a user has requested cancel, do not begin the
            # next expensive sync before handing the lock to that manual action.
            for campaign in campaigns:
                if self._manual_action_pending.is_set():
                    break
                if not self.action_lock.acquire(timeout=0.05):
                    break
                try:
                    if self._manual_action_pending.is_set():
                        break
                    try:
                        self._sync_campaign(campaign)
                    except Exception as exc:
                        with self.lock:
                            campaign["lastError"] = str(exc)
                            campaign["updatedAt"] = now_iso()
                            self._set_safe_mode_locked(f"{campaign['coin']} sync error: {exc}")
                            self._event_locked(
                                "error",
                                f"{campaign['coin']}: синхронизация LIVE: {exc}",
                                campaignId=campaign.get("id"),
                            )
                            self._save_locked()
                finally:
                    self.action_lock.release()

            monotonic_now = time.monotonic()
            if monotonic_now < next_global_check or self._manual_action_pending.is_set():
                continue
            if not self.action_lock.acquire(timeout=0.05):
                continue
            try:
                if self._manual_action_pending.is_set():
                    continue
                try:
                    self._scan_global_risks()
                except Exception as exc:
                    with self.lock:
                        self._set_safe_mode_locked(f"Глобальная сверка не выполнена: {exc}")
                        self._event_locked("error", f"Глобальная сверка LIVE: {exc}")
                        self._save_locked()
                next_global_check = time.monotonic() + self.config.global_check_interval
            finally:
                self.action_lock.release()

    def _sync_research_once(self) -> None:
        try:
            script = Path(__file__).resolve().parents[1] / "scripts" / "galka-research-sync.sh"
            if script.exists():
                import subprocess

                subprocess.run(
                    ["bash", str(script), "--once"],
                    cwd=script.parent.parent,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=45,
                    check=False,
                )
        except Exception:
            pass

    def _campaign_by_id_locked(self, campaign_id: str | None) -> dict[str, Any] | None:
        if not campaign_id:
            return None
        for campaign in self.state.get("campaigns", {}).values():
            if str(campaign.get("id") or "") == str(campaign_id):
                return campaign
        return None

    def _event_locked(self, event_type: str, message: str, **meta: Any) -> None:
        super()._event_locked(event_type, message, **meta)
        try:
            campaign = self._campaign_by_id_locked(meta.get("campaignId"))
            self.research_journal.append_event(event_type, message, meta, campaign)
        except Exception:
            pass

    def _apply_new_fills(self, campaign: dict[str, Any], fills: list[dict[str, Any]]) -> None:
        before = set(campaign.get("seenFills", []))
        super()._apply_new_fills(campaign, fills)
        for fill in fills:
            try:
                if self._fill_key(fill) not in before:
                    self.research_journal.append_fill(campaign, fill)
            except Exception:
                pass
        self.research_journal.upsert_campaign(campaign, reason="fills_updated")

    def _is_galka_target(self, campaign: dict[str, Any], order: dict[str, Any]) -> bool:
        """Accept both native trigger TP and rounded fallback reduce-only limits.

        Fallback targets are rounded to the exchange-valid price before placement.
        Comparing them against the raw user GALKA price caused false
        `Owned target orders have wrong parameters` errors whenever rounding moved
        the price by a tick (for example 1912.66 -> 1912.7).
        """
        if not order.get("reduceOnly") or str(order.get("side") or "") not in {"A", "Sell", "sell"}:
            return False
        coin = str(campaign.get("coin") or "")
        expected = round_perp_price(
            float(campaign["galkaPrice"]), self.gateway.sz_decimals(coin)
        )
        trigger = float(order.get("triggerPrice") or 0)
        limit_price = float(order.get("price") or 0)
        actual = trigger if trigger > 0 else limit_price
        tolerance = max(1e-9, abs(expected) * 1e-10)
        return actual > 0 and abs(actual - expected) <= tolerance

    def _capture_setup_context(self, coin: str, campaign_id: str) -> None:
        try:
            mid = float(self.gateway.mids().get(coin) or 0)
            with self.lock:
                campaign = self._campaign_by_id_locked(campaign_id)
                if not campaign:
                    return
                if mid > 0:
                    campaign["setupMidPrice"] = mid
                    galka = float(campaign.get("galkaPrice") or 0)
                    if galka > 0:
                        campaign["setupDistancePct"] = (mid / galka - 1.0) * 100.0
                campaign["weightedAverage"] = campaign.get("weightedAverage")
                self._save_locked()
                snapshot = dict(campaign)
            self.research_journal.upsert_campaign(snapshot, reason="placement_context")
        except Exception:
            pass

    def create_campaign(self, coin: str, galka_price: float, confirmation: str) -> dict[str, Any]:
        result = super().create_campaign(coin, galka_price, confirmation)
        self.research_journal.upsert_campaign(result, reason="created")
        threading.Thread(
            target=self._capture_setup_context,
            args=(str(result.get("coin") or coin), str(result.get("id") or "")),
            name=f"galka-research-setup-{result.get('coin') or coin}",
            daemon=True,
        ).start()
        return result

    def _append_campaign_journal_locked(self, campaign: dict[str, Any], reason: str) -> None:
        super()._append_campaign_journal_locked(campaign, reason)
        self.research_journal.upsert_campaign(campaign, reason=f"recovery:{reason}")

    def _finish_cycle(self, campaign: dict[str, Any]) -> None:
        deepest = int(campaign.get("cycleDeepest") or 0)
        force_finish_after_l1 = (
            deepest == 1
            and not campaign.get("autoRearmBlocked")
            and not campaign.get("abortAfterClose")
        )
        if force_finish_after_l1:
            campaign["abortAfterClose"] = True

        super()._finish_cycle(campaign)

        with self.lock:
            had_position = (
                bool(campaign.get("hadPosition"))
                or deepest > 0
                or any(
                    float(level.get("filledSize") or 0) > 0
                    for level in campaign.get("levels", [])
                )
            )
            if had_position:
                campaign["hadPosition"] = True
            if force_finish_after_l1 and campaign.get("status") == "error_closed":
                campaign["status"] = "completed"
                campaign["abortAfterClose"] = False
                campaign["lastError"] = None
                campaign["recoveryReason"] = None
                self._event_locked(
                    "live",
                    f"{campaign['coin']}: L1 закрыта на GALKA, вся кампания завершена; L1 rearm отключён",
                    campaignId=campaign["id"],
                    deepest=1,
                    pnl=campaign.get("finalClosedPnl"),
                )
            self._save_locked()

        self.research_journal.upsert_campaign(campaign, reason="cycle_finished_no_l1_rearm")

    def cancel_waiting_campaign(self, coin: str) -> dict[str, Any]:
        """Cancel an empty GALKA with one fewer normal-path exchange read.

        Current level OIDs are known from placement and are safe to use as the
        first cancellation attempt. If that attempt is stale or incomplete we
        immediately fall back to a fresh venue order snapshot. We still require a
        fresh flat account read and a final fresh owned-order verification before
        declaring the campaign canceled.
        """
        normalized = self._coin(coin)
        self._require_live_writes()
        success = False
        campaign: dict[str, Any] | None = None
        result: dict[str, Any] | None = None
        lock_acquired = False
        trace_started = False
        wait_ms = 0.0
        entry_oids: list[int] = []
        target_oids: list[int] = []

        self._manual_action_pending.set()
        wait_started = time.monotonic()
        try:
            self.action_lock.acquire()
            lock_acquired = True
            wait_ms = round((time.monotonic() - wait_started) * 1000, 1)
            self.gateway.begin_trace("cancel_campaign")
            trace_started = True

            with self.lock:
                campaign = self._active_campaign_locked(normalized)
                if not campaign:
                    raise LiveEngineError(f"Для {normalized} нет активной GALKA")
                campaign["autoRearmBlocked"] = True
                campaign["status"] = "canceling"
                campaign["updatedAt"] = now_iso()
                self._save_locked()
                levels = list(campaign.get("levels", []))

            expected_entries = len(levels)
            entry_oids = sorted({
                int(level.get("oid") or 0)
                for level in levels
                if int(level.get("oid") or 0) > 0
                and str(level.get("status") or "") in {"resting", "partial", "waitingForFill"}
            })

            # Normal waiting campaigns have all eight live entry OIDs already in
            # local state. That lets us skip the expensive pre-cancel /info read.
            # Any incomplete/stale local ownership falls back to venue discovery.
            need_discovery = len(entry_oids) < expected_entries
            if not need_discovery and entry_oids:
                try:
                    self.gateway.cancel_oids(normalized, entry_oids)
                except Exception:
                    need_discovery = True
            if need_discovery:
                open_orders = self.gateway.fresh_open_orders(normalized)
                entry_oids = [
                    int(row.get("oid") or 0)
                    for row in open_orders
                    if self._entry_owner(campaign, row) is not None and int(row.get("oid") or 0) > 0
                ]
                if entry_oids:
                    self.gateway.cancel_oids(normalized, entry_oids)

            # Keep targets alive until a fresh venue account snapshot proves flat.
            time.sleep(0.08)
            account = self.gateway.fresh_account_state()
            actual = self._position_size(account, normalized)
            tolerance = self._size_tolerance(normalized)

            if abs(actual) > tolerance:
                latest_orders = self.gateway.fresh_open_orders(normalized)
                self._enter_recovery(
                    campaign,
                    "Быстрая отмена остановлена: во время снятия входов биржа показала позицию",
                    actual,
                    latest_orders,
                )
                raise LiveEngineError(
                    "Во время отмены появился реальный fill. Входы сняты, защитные TP сохранены; включён recovery."
                )

            # One authoritative post-cancel read doubles as target discovery and
            # final verification. Any residual owned order is canceled once and
            # verified again; no optimistic local-only completion is allowed.
            remaining = self.gateway.fresh_open_orders(normalized)
            owned_remaining = self._owned_open_orders(campaign, remaining)
            if owned_remaining:
                target_oids = [
                    int(row.get("oid") or 0)
                    for row in owned_remaining
                    if self._target_owner(campaign, row) is not None and int(row.get("oid") or 0) > 0
                ]
                retry_oids = [
                    int(row.get("oid") or 0)
                    for row in owned_remaining
                    if int(row.get("oid") or 0) > 0
                ]
                if retry_oids:
                    self.gateway.cancel_oids(normalized, retry_oids)
                    time.sleep(0.08)
                remaining = self.gateway.fresh_open_orders(normalized)
                owned_remaining = self._owned_open_orders(campaign, remaining)

            if owned_remaining:
                self._enter_recovery(
                    campaign,
                    "Быстрая отмена не получила подтверждение удаления всех owned-ордеров",
                    0.0,
                    remaining,
                )
                raise LiveEngineError(
                    "Биржа не подтвердила удаление всех ордеров GALKA; включён recovery."
                )

            with self.lock:
                campaign["status"] = "canceled"
                campaign["actualPositionSize"] = 0.0
                campaign["managedNetSize"] = 0.0
                campaign["completedAt"] = now_iso()
                campaign["updatedAt"] = now_iso()
                self._event_locked(
                    "live",
                    f"{normalized}: GALKA быстро отменена без позиции",
                    campaignId=campaign["id"],
                    fastCancel=True,
                    entryOrders=len(entry_oids),
                    targetOrders=len(target_oids),
                )
                self._save_locked()
                result = deepcopy(campaign)
            success = True

        except Exception as exc:
            if campaign is not None and campaign.get("status") != "recovery":
                try:
                    latest_account = self.gateway.fresh_account_state()
                    latest_orders = self.gateway.fresh_open_orders(normalized)
                    latest_actual = self._position_size(latest_account, normalized)
                    self._enter_recovery(
                        campaign,
                        f"Ошибка быстрой отмены: {exc}",
                        latest_actual,
                        latest_orders,
                    )
                except Exception:
                    pass
            if isinstance(exc, LiveEngineError):
                raise
            raise LiveEngineError(str(exc)) from exc
        finally:
            if lock_acquired:
                self.action_lock.release()
            self._manual_action_pending.clear()
            if trace_started:
                trace = self.gateway.finish_trace()
                trace.setdefault("stages", []).insert(
                    0,
                    {"stage": "action_lock_wait", "ms": wait_ms, "ok": True},
                )
                trace["totalMs"] = round(float(trace.get("totalMs") or 0) + wait_ms, 1)
                self._record_latency(
                    "быстрая отмена GALKA",
                    normalized,
                    trace,
                    success,
                )

        assert result is not None
        self.research_journal.upsert_campaign(result, reason="cancelled_fast")
        return result

    def close_near_market(self, coin: str, confirmation: str) -> dict[str, Any]:
        result = super().close_near_market(coin, confirmation)
        with self.lock:
            campaign = self._campaign_locked(coin)
            if campaign:
                self.research_journal.upsert_campaign(campaign, reason="manual_exit_requested")
        return result

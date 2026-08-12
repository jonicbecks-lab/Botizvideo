"""Production-safe compatibility layer for Galka LIVE.

Keeps the exchange-tested pair-wise normalTpsl placement path, fixes validation
of rounded fallback targets, adds a fast two-phase cancel path for empty GALKA
campaigns, and records non-secret research data independently from trading state.
"""

from __future__ import annotations

import threading
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

from .engine import LiveEngineError, now_iso
from .hyperliquid_compat import (
    CompatibleGalkaLiveEngine as _OptimizedEngine,
    CompatibleHyperliquidGateway as _BatchGateway,
)
from .hyperliquid_gateway import EntryWithTarget
from .live_ladder import LadderLevel, round_perp_price
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
        super().__init__(config, gateway)

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
        super()._finish_cycle(campaign)
        self.research_journal.upsert_campaign(campaign, reason="cycle_finished_or_rearmed")

    def cancel_waiting_campaign(self, coin: str) -> dict[str, Any]:
        """Fast, race-safe cancellation for a GALKA that has no position.

        The old path performed a full campaign sync, then multiple account/order
        confirmation loops. For an empty waiting ladder that made cancellation
        take roughly as long as recovery. This path instead:

        1. Reads current open orders once.
        2. Cancels owned ENTRY orders first in one batch, preventing new exposure.
        3. Reads the account once after entry cancellation.
        4. Only if the account is flat, cancels owned TARGET orders in one batch.
        5. Performs one final open-order verification, with one retry for delayed
           cancellation visibility.

        Targets are deliberately kept alive until flatness is confirmed, so a
        fill racing with the cancel request remains protected.
        """
        normalized = self._coin(coin)
        self._require_live_writes()
        self.gateway.begin_trace("cancel_campaign")
        success = False
        campaign: dict[str, Any] | None = None
        result: dict[str, Any] | None = None

        try:
            with self.action_lock:
                with self.lock:
                    campaign = self._active_campaign_locked(normalized)
                    if not campaign:
                        raise LiveEngineError(f"Для {normalized} нет активной GALKA")
                    campaign["autoRearmBlocked"] = True
                    campaign["status"] = "canceling"
                    campaign["updatedAt"] = now_iso()
                    self._save_locked()

                # Phase 1: stop new exposure first. Do not cancel TP targets yet.
                open_orders = self.gateway.fresh_open_orders(normalized)
                entry_oids = [
                    int(row.get("oid") or 0)
                    for row in open_orders
                    if self._entry_owner(campaign, row) is not None and int(row.get("oid") or 0) > 0
                ]
                target_oids = [
                    int(row.get("oid") or 0)
                    for row in open_orders
                    if self._target_owner(campaign, row) is not None and int(row.get("oid") or 0) > 0
                ]

                if entry_oids:
                    self.gateway.cancel_oids(normalized, entry_oids)

                # Give the exchange/event stream a very short propagation window;
                # the cancel response itself remains authoritative for submission.
                time.sleep(0.12)
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

                # Phase 2: once flatness is confirmed, targets can be removed safely.
                if target_oids:
                    self.gateway.cancel_oids(normalized, target_oids)

                time.sleep(0.12)
                remaining = self.gateway.fresh_open_orders(normalized)
                owned_remaining = self._owned_open_orders(campaign, remaining)

                # One compact retry handles delayed order visibility without the
                # old 4x account/order confirmation loop.
                if owned_remaining:
                    retry_oids = [int(row.get("oid") or 0) for row in owned_remaining if int(row.get("oid") or 0) > 0]
                    if retry_oids:
                        self.gateway.cancel_oids(normalized, retry_oids)
                        time.sleep(0.12)
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
            assert result is not None
            self.research_journal.upsert_campaign(result, reason="cancelled_fast")
            return result

        except Exception as exc:
            # If the fast path itself fails before recovery was established,
            # reconstruct current exchange state and fail closed rather than
            # guessing that cancellation succeeded.
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
            self._record_latency(
                "быстрая отмена GALKA",
                normalized,
                self.gateway.finish_trace(),
                success,
            )

    def close_near_market(self, coin: str, confirmation: str) -> dict[str, Any]:
        result = super().close_near_market(coin, confirmation)
        with self.lock:
            campaign = self._campaign_locked(coin)
            if campaign:
                self.research_journal.upsert_campaign(campaign, reason="manual_exit_requested")
        return result

from __future__ import annotations

import time
from copy import deepcopy
from typing import Any

from .engine import ACTIVE_STATUSES, RECOVERY_STATUS, LiveEngineError, now_iso
from .galka_v2_engine import GalkaV2Engine, GalkaV2Gateway
from .hyperliquid_gateway import HyperliquidGateway


class FastGalkaV2Gateway(GalkaV2Gateway):
    """V2 gateway with one safe duplicate account read removed during placement.

    create_campaign_v2 calls preview_v2 and then immediately asks for the same
    account snapshot again before any exchange mutation. Reuse only that second
    read. Any account read after leverage/order mutations remains fresh. Order
    verification after the batch stays authoritative and still hits Hyperliquid.
    """

    _PLACEMENT_TRACE_NAMES = {"create_campaign", "create_campaign_v2"}

    def fresh_account_state(self) -> dict[str, Any]:
        with self._trace_lock:
            trace_name = self._trace_name
            read_no = self._trace_account_reads
            snapshot = self._trace_account_snapshot
            snapshot_age = time.monotonic() - self._trace_account_snapshot_at
            self._trace_account_reads += 1

        if (
            trace_name in self._PLACEMENT_TRACE_NAMES
            and read_no == 1
            and snapshot is not None
            and snapshot_age < 2.0
        ):
            return self._timed("fresh_account_state_reused", lambda: deepcopy(snapshot))

        result = self._timed(
            "fresh_account_state",
            lambda: HyperliquidGateway.fresh_account_state(self),
        )
        if trace_name in self._PLACEMENT_TRACE_NAMES and read_no == 0:
            with self._trace_lock:
                self._trace_account_snapshot = deepcopy(result)
                self._trace_account_snapshot_at = time.monotonic()
        return result


class FastGalkaV2Engine(GalkaV2Engine):
    """Keep V2 safety checks but avoid redundant network round-trips.

    The fast cancel path is deliberately narrow: it is used only for a pristine
    V2 campaign that has never filled. Filled/partial/recovery campaigns continue
    through the original hardened cancellation flow.
    """

    def _pristine_waiting_cancel_candidate(
        self,
        campaign: dict[str, Any] | None,
    ) -> bool:
        if not self._is_v2(campaign) or not campaign:
            return False
        if campaign.get("status") != "waiting":
            return False
        if campaign.get("v2UpperDone") or campaign.get("v2LowerActivated"):
            return False
        if campaign.get("hadPosition"):
            return False
        if abs(float(campaign.get("managedNetSize") or 0)) > 1e-12:
            return False
        if abs(float(campaign.get("actualPositionSize") or 0)) > 1e-12:
            return False
        levels = list(campaign.get("levels") or [])
        if len(levels) != 9:
            return False
        for level in levels:
            if float(level.get("filledSize") or 0) > 1e-12:
                return False
            if int(level.get("oid") or 0) <= 0:
                return False
            if str(level.get("status") or "") not in {"resting", "new"}:
                return False
        return True

    def cancel_waiting_campaign(self, coin: str) -> dict[str, Any]:
        normalized = self._coin(coin)
        self._require_live_writes()

        with self.lock:
            initial = self._active_campaign_locked(normalized)
            fast_candidate = self._pristine_waiting_cancel_candidate(initial)

        # Any campaign with a fill/history ambiguity keeps the original, slower
        # reconciliation-heavy path.
        if not fast_candidate:
            return super().cancel_waiting_campaign(normalized)

        self.gateway.begin_trace("cancel_campaign_v2_fast")
        success = False
        try:
            result = self._cancel_pristine_v2_fast(normalized)
            success = True
            return result
        finally:
            self._record_latency(
                "отмена GALKA V2 fast",
                normalized,
                self.gateway.finish_trace(),
                success,
            )

    def _cancel_pristine_v2_fast(self, coin: str) -> dict[str, Any]:
        with self.action_lock:
            with self.lock:
                campaign = self._active_campaign_locked(coin)
                if not campaign:
                    raise LiveEngineError(f"Для {coin} нет активной GALKA")
                if not self._pristine_waiting_cancel_candidate(campaign):
                    raise LiveEngineError(
                        "GALKA изменилась перед отменой; повтори отмену для полной безопасной сверки"
                    )

            # One authoritative pre-cancel snapshot. Open orders are needed to
            # reject foreign/manual orders and to prove all nine V2 entries are
            # still present before taking the fast path.
            account = self.gateway.fresh_account_state()
            open_orders = self.gateway.fresh_open_orders(coin)
            actual = self._position_size(account, coin)
            tolerance = self._size_tolerance(coin)

            if abs(actual) > tolerance:
                self._enter_recovery(
                    campaign,
                    "Быстрая отмена остановлена: биржа уже показывает позицию",
                    actual,
                    open_orders,
                )
                raise LiveEngineError(
                    "Есть реальная позиция. Быстрая отмена запрещена; включён recovery."
                )

            foreign = self._foreign_open_orders(campaign, open_orders)
            if foreign:
                self._enter_recovery(
                    campaign,
                    f"Быстрая отмена остановлена: посторонних ордеров {len(foreign)}",
                    actual,
                    open_orders,
                )
                raise LiveEngineError("Обнаружены посторонние ордера; включён recovery")

            owned = self._owned_open_orders(campaign, open_orders)
            expected_oids = {
                int(level.get("oid") or 0)
                for level in campaign.get("levels", [])
                if int(level.get("oid") or 0) > 0
            }
            live_oids = {int(row.get("oid") or 0) for row in owned if int(row.get("oid") or 0) > 0}
            if len(expected_oids) != 9 or live_oids != expected_oids:
                raise LiveEngineError(
                    "Набор ордеров изменился перед быстрой отменой; повтори команду для полной сверки"
                )

            with self.lock:
                if campaign.get("status") not in ACTIVE_STATUSES:
                    raise LiveEngineError(
                        f"Кампания уже завершила переход в статус {campaign.get('status')}"
                    )
                campaign["status"] = "canceling"
                campaign["updatedAt"] = now_iso()
                self._save_locked()

            try:
                # bulk_cancel itself returns one venue acknowledgement per oid.
                # Therefore we do not immediately repeat an open-orders query.
                self.gateway.cancel_oids(coin, sorted(expected_oids))
            except Exception as exc:
                latest_account = self.gateway.fresh_account_state()
                latest_orders = self.gateway.fresh_open_orders(coin)
                self._enter_recovery(
                    campaign,
                    f"Ошибка быстрой отмены: {exc}",
                    self._position_size(latest_account, coin),
                    latest_orders,
                )
                raise LiveEngineError(str(exc)) from exc

            # A final fresh position read closes the race where an entry could
            # fill between the pre-check and bulk_cancel acknowledgement.
            final_account = self.gateway.fresh_account_state()
            final_actual = self._position_size(final_account, coin)
            if abs(final_actual) > tolerance:
                latest_orders = self.gateway.fresh_open_orders(coin)
                self._enter_recovery(
                    campaign,
                    "Позиция появилась во время быстрой отмены",
                    final_actual,
                    latest_orders,
                )
                raise LiveEngineError(
                    "Позиция появилась во время отмены; кампания переведена в recovery"
                )

            with self.lock:
                if campaign.get("status") == RECOVERY_STATUS:
                    raise LiveEngineError("Кампания перешла в recovery во время отмены")
                campaign["status"] = "canceled"
                campaign["completedAt"] = now_iso()
                campaign["updatedAt"] = now_iso()
                campaign["actualPositionSize"] = 0.0
                self._event_locked(
                    "live",
                    f"{coin}: GALKA V2 быстро отменена без позиции",
                    campaignId=campaign["id"],
                )
                self._save_locked()
                return deepcopy(campaign)

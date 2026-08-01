from __future__ import annotations

import time
from typing import Any

from .engine import RECOVERY_STATUS, GalkaLiveEngine, now_iso
from .live_ladder import round_perp_price


class ReliableGalkaLiveEngine(GalkaLiveEngine):
    """Small production fixes around recovery without changing trading strategy.

    Hyperliquid can expose a new position snapshot before the corresponding fill
    appears in the fills endpoint. The base engine correctly fails closed, but a
    single transient mismatch should first receive a few bounded, fresh reads.
    """

    _FILL_CATCHUP_DELAYS = (0.10, 0.25, 0.50)

    def _is_galka_target(self, campaign: dict[str, Any], order: dict[str, Any]) -> bool:
        if not order.get("reduceOnly") or order.get("side") != "A":
            return False
        expected = round_perp_price(
            float(campaign["galkaPrice"]),
            self.gateway.sz_decimals(campaign["coin"]),
        )
        price = float(order.get("triggerPrice") or order.get("price") or 0)
        tolerance = max(1e-9, abs(expected) * 1e-10)
        return abs(price - expected) <= tolerance

    def _retry_delayed_owned_fills(self, campaign: dict[str, Any]) -> bool:
        """Retry only a position/owned-fill mismatch using fresh venue reads."""

        coin = campaign["coin"]
        tolerance = self._size_tolerance(coin)
        for delay in self._FILL_CATCHUP_DELAYS:
            time.sleep(delay)
            try:
                open_orders = self.gateway.fresh_open_orders(coin)
                account = self.gateway.fresh_account_state()
                with self.lock:
                    cursor = max(
                        0,
                        int(
                            campaign.get("fillCursorMs")
                            or int(campaign.get("createdMs") or 0) - 60_000
                        ),
                    )
                fills = [
                    row
                    for row in self.gateway.fills_since(cursor)
                    if row.get("coin") == coin
                ]
                prepared = self._prepare_fill_owners(campaign, fills)
            except Exception:
                return False

            with self.lock:
                self._register_delayed_orders(campaign, open_orders)
                self._apply_new_fills(campaign, prepared)
                actual = self._position_size(account, coin)
                managed = float(campaign.get("managedNetSize") or 0)
                campaign["actualPositionSize"] = actual
                campaign["updatedAt"] = now_iso()

                foreign = self._foreign_open_orders(campaign, open_orders)
                short_position = actual < -tolerance
                fills_saturated = len(fills) >= 1990
                mismatch = abs(actual - managed) > tolerance
                resolved = not (
                    foreign or short_position or fills_saturated or mismatch
                )
                if resolved:
                    campaign["lastError"] = None
                    self._event_locked(
                        "sync",
                        f"{coin}: запоздавшие owned fills подтверждены повторной сверкой",
                        campaignId=campaign.get("id"),
                        actualPosition=actual,
                        managedPosition=managed,
                    )
                self._save_locked()
                if resolved:
                    return True
        return False

    def _mark_entry_statuses_after_recovery(
        self,
        campaign: dict[str, Any],
        open_orders: list[dict[str, Any]],
    ) -> None:
        open_entry_levels = {
            owner
            for row in open_orders
            if (owner := self._entry_owner(campaign, row)) is not None
        }
        with self.lock:
            for level in campaign.get("levels", []):
                index = int(level.get("index") or 0)
                filled = float(level.get("filledSize") or 0)
                requested = float(level.get("size") or 0)
                if requested > 0 and filled >= requested - 1e-12:
                    level["status"] = "filled"
                elif index in open_entry_levels:
                    level["status"] = "partial" if filled > 0 else "resting"
                else:
                    level["status"] = "canceled"
            campaign["updatedAt"] = now_iso()
            self._save_locked()

    def _enter_recovery(
        self,
        campaign: dict[str, Any],
        reason: str,
        actual_size: float,
        open_orders: list[dict[str, Any]],
    ) -> None:
        if reason.startswith("Расхождение позиции:"):
            if self._retry_delayed_owned_fills(campaign):
                return

        super()._enter_recovery(campaign, reason, actual_size, open_orders)
        try:
            latest_orders = self.gateway.fresh_open_orders(campaign["coin"])
        except Exception:
            latest_orders = []
        self._mark_entry_statuses_after_recovery(campaign, latest_orders)

    def _set_safe_mode_locked(self, reason: str) -> None:
        is_network_error = (
            "Hyperliquid read failed" in reason
            or "NameResolutionError" in reason
            or "Failed to resolve" in reason
        )
        recovery_campaign = next(
            (
                campaign
                for campaign in self.state.get("campaigns", {}).values()
                if campaign.get("status") == RECOVERY_STATUS
            ),
            None,
        )
        if is_network_error and recovery_campaign:
            system = self.state.setdefault("system", {})
            system["safeMode"] = True
            system["lastNetworkError"] = reason
            system["lastNetworkErrorAt"] = now_iso()
            recovery_reason = recovery_campaign.get("recoveryReason") or "требуется сверка"
            system["safeModeReason"] = (
                f"{recovery_campaign.get('coin', '?')} recovery: {recovery_reason}"
            )
            return
        super()._set_safe_mode_locked(reason)

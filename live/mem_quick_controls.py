from __future__ import annotations

import math
from copy import deepcopy
from typing import Any

from .engine import LiveEngineError
from .live_ladder import round_perp_price

MOVE_TP_PREFIX = "MOVE_GALKA_MEM_TP:"


def install_quick_controls(base_engine_cls):
    if getattr(base_engine_cls, "__galka_mem_quick_controls__", False):
        return base_engine_cls

    class QuickControlMemEngine(base_engine_cls):
        __galka_mem_quick_controls__ = True

        @staticmethod
        def _manual_upper_tp(campaign: dict[str, Any]) -> float:
            try:
                value = float(campaign.get("manualUpperTakeProfit") or 0.0)
            except (TypeError, ValueError):
                return 0.0
            return value if math.isfinite(value) and value > 0 else 0.0

        def _ensure_target(
            self,
            campaign: dict[str, Any],
            basket: str,
            desired_size: float,
            desired_price: float,
            open_orders: list[dict[str, Any]],
        ) -> None:
            if basket == "upper":
                manual = self._manual_upper_tp(campaign)
                if manual > 0:
                    desired_price = manual
            return super()._ensure_target(
                campaign,
                basket,
                desired_size,
                desired_price,
                open_orders,
            )

        def _set_manual_upper_tp(self, requested_price: float) -> dict[str, Any]:
            self._require_live()
            with self.action_lock:
                with self.lock:
                    if self.state.get("system", {}).get("safeMode"):
                        raise LiveEngineError(
                            f"GALKA MEM SAFE MODE: {self.state['system'].get('safeModeReason') or 'reconcile first'}"
                        )
                    campaign = self._active_campaign_locked()
                    if not campaign:
                        raise LiveEngineError("No active GALKA MEM campaign")
                    coin = campaign["coin"]

                account = self.gateway.fresh_account_state()
                position = self._position_size(account, coin)
                tolerance = self._size_tolerance(coin)
                if position <= tolerance:
                    raise LiveEngineError("Moving TP requires an open long position")

                upper_open = self._logical_open_size(campaign, "upper")
                if upper_open <= tolerance:
                    raise LiveEngineError("There is no open upper-basket inventory to protect with TP")

                try:
                    requested = float(requested_price)
                except (TypeError, ValueError) as exc:
                    raise LiveEngineError("Invalid TP price") from exc
                if not math.isfinite(requested) or requested <= 0:
                    raise LiveEngineError("TP price must be positive and finite")
                requested = round_perp_price(requested, self.gateway.sz_decimals(coin))

                mid = float(self.gateway.mids().get(coin) or 0.0)
                if mid <= 0 or requested <= mid:
                    raise LiveEngineError(
                        f"TP {requested:g} must remain above current market {mid:g}"
                    )

                open_orders = self.gateway.fresh_open_orders(coin)
                # Place/modify the real reduce-only TP first. Persist the manual
                # override only after the exchange confirms the order.
                super()._ensure_target(
                    campaign,
                    "upper",
                    upper_open,
                    requested,
                    open_orders,
                )
                with self.lock:
                    campaign["manualUpperTakeProfit"] = requested
                    campaign["upperTakeProfit"] = requested
                    self._event_locked(
                        "ok",
                        f"GALKA MEM {coin}: upper TP moved to {requested:g}",
                    )
                    self._save_locked()
                    return deepcopy(campaign)

        def reconcile(self, confirmation: str) -> dict[str, Any]:
            if confirmation.startswith(MOVE_TP_PREFIX):
                try:
                    requested = float(confirmation[len(MOVE_TP_PREFIX):])
                except (TypeError, ValueError) as exc:
                    raise LiveEngineError("Invalid TP price") from exc
                self._set_manual_upper_tp(requested)
                return self.status()
            return super().reconcile(confirmation)

        def _sync_campaign(self) -> None:
            super()._sync_campaign()
            with self.lock:
                campaign = self._active_campaign_locked()
                if not campaign:
                    return
                manual = self._manual_upper_tp(campaign)
                if manual > 0 and campaign.get("upperTakeProfit") != manual:
                    campaign["upperTakeProfit"] = manual
                    self._save_locked()

        def status(self) -> dict[str, Any]:
            payload = super().status()
            campaign = payload.get("campaign")
            if not campaign:
                return payload
            coin = campaign.get("coin")
            account = payload.get("accountState") or {}
            position = float(((account.get("positions") or {}).get(coin) or {}).get("size") or 0.0)
            try:
                upper_open = self._logical_open_size(campaign, "upper")
            except Exception:
                upper_open = 0.0
            tolerance = self._size_tolerance(coin) if coin else 0.0
            campaign["quickControls"] = {
                "canMoveTp": bool(coin and position > tolerance and upper_open > tolerance),
                "manualTp": self._manual_upper_tp(campaign) or None,
                "tp": campaign.get("upperTakeProfit"),
            }
            return payload

    QuickControlMemEngine.__name__ = base_engine_cls.__name__
    QuickControlMemEngine.__qualname__ = base_engine_cls.__qualname__
    return QuickControlMemEngine

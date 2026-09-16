from __future__ import annotations

from typing import Any

from .engine import RECOVERY_STATUS
from .hyperliquid_compat import CompatibleGalkaLiveEngine, CompatibleHyperliquidGateway


class GalkaClassicGateway(CompatibleHyperliquidGateway):
    """Classic lower-only GALKA gateway."""


class GalkaClassicEngine(CompatibleGalkaLiveEngine):
    """Classic 8-level GALKA.

    Entries are only below GALKA. Any owned GALKA take-profit that returns the
    real position to flat ends the campaign; L1 is never rearmed automatically.
    """

    def _finish_cycle(self, campaign: dict[str, Any]) -> None:
        coin = campaign["coin"]
        deepest = int(campaign.get("cycleDeepest") or 0)
        net_cycle = float(campaign.get("cycleClosedPnl") or 0) - float(
            campaign.get("cycleFees") or 0
        )

        if deepest <= 0:
            self._enter_recovery(
                campaign,
                "Позиция закрыта без подтверждённого owned GALKA TP",
                0.0,
                self.gateway.fresh_open_orders(coin),
            )
            return

        try:
            self._cancel_owned_orders(campaign)
            ok, actual, remaining = self._confirm_flat_and_clean(campaign, reads=2)
        except Exception as exc:
            latest_account = self.gateway.fresh_account_state()
            latest_orders = self.gateway.fresh_open_orders(coin)
            self._enter_recovery(
                campaign,
                f"Финальная очистка classic GALKA не подтверждена: {exc}",
                self._position_size(latest_account, coin),
                latest_orders,
            )
            return

        if not ok:
            self._enter_recovery(
                campaign,
                "Финальная очистка classic GALKA не подтверждена биржей",
                actual,
                remaining,
            )
            return

        with self.lock:
            if campaign.get("status") == RECOVERY_STATUS:
                return
            campaign["status"] = "completed"
            campaign["completedAt"] = self._now_iso_compat()
            campaign["hadPosition"] = False
            campaign["managedNetSize"] = 0.0
            campaign["actualPositionSize"] = 0.0
            campaign["finalClosedPnl"] = net_cycle
            campaign["autoRearmBlocked"] = True
            self._event_locked(
                "live",
                f"{coin}: L{deepest} закрыта на GALKA; кампания завершена без rearm",
                campaignId=campaign["id"],
                deepest=deepest,
                pnl=net_cycle,
            )
            self._save_locked()

    @staticmethod
    def _now_iso_compat() -> str:
        from .engine import now_iso

        return now_iso()

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .engine import ACTIVE_STATUSES
from .hyperliquid_safe_compat import SafeCompatibleGalkaLiveEngine
from .research_recorder import GalkaResearchRecorder


class ResearchCompatibleGalkaLiveEngine(SafeCompatibleGalkaLiveEngine):
    """Safe LIVE engine with an isolated, best-effort research sidecar.

    The recorder never places/cancels orders and is not consulted by any trading
    decision. All public recorder hooks are fire-and-forget queue operations.
    """

    def __init__(self, config: Any, gateway: Any):
        self.research_recorder = GalkaResearchRecorder(config)
        super().__init__(config, gateway)

    def start(self) -> None:
        # Re-arm persisted sessions before initial reconciliation can emit
        # lifecycle/fill events. The market stream itself is independent from
        # the authenticated trading gateway.
        try:
            with self.lock:
                active = [
                    deepcopy(campaign)
                    for campaign in self.state.get("campaigns", {}).values()
                    if campaign.get("status") in ACTIVE_STATUSES
                ]
            self.research_recorder.restore_active_campaigns(active)
            self.research_recorder.start()
        except Exception:
            # Research must never block LIVE startup.
            pass
        super().start()

    def stop(self) -> None:
        try:
            super().stop()
        finally:
            try:
                self.research_recorder.stop()
            except Exception:
                pass

    def _new_campaign(
        self,
        campaign_id: str,
        coin: str,
        galka_price: float,
        preview: dict[str, Any],
        levels: list[Any],
    ) -> dict[str, Any]:
        campaign = super()._new_campaign(campaign_id, coin, galka_price, preview, levels)
        try:
            # Session construction is local-only; actual file writes happen on
            # the recorder worker. Arming here prevents missing a fast cross
            # while the eight order pairs are still being submitted.
            self.research_recorder.arm_campaign(campaign)
        except Exception:
            pass
        return campaign

    def create_campaign(self, coin: str, galka_price: float, confirmation: str) -> dict[str, Any]:
        result = super().create_campaign(coin, galka_price, confirmation)
        try:
            self.research_recorder.on_campaign_snapshot(result, "created_and_orders_confirmed")
        except Exception:
            pass
        return result

    def _event_locked(self, event_type: str, message: str, **meta: Any) -> None:
        super()._event_locked(event_type, message, **meta)
        try:
            campaign = self._campaign_by_id_locked(meta.get("campaignId"))
            if campaign is not None:
                self.research_recorder.on_galka_event(
                    event_type,
                    message,
                    meta,
                    campaign,
                )
        except Exception:
            pass

    def _apply_new_fills(self, campaign: dict[str, Any], fills: list[dict[str, Any]]) -> None:
        before = set(campaign.get("seenFills", []))
        super()._apply_new_fills(campaign, fills)
        try:
            for fill in fills:
                if self._fill_key(fill) not in before:
                    self.research_recorder.on_execution_fill(campaign, fill)
            self.research_recorder.on_campaign_snapshot(campaign, "fills_updated")
        except Exception:
            pass

    def status(self) -> dict[str, Any]:
        result = super().status()
        try:
            result["researchRecorder"] = self.research_recorder.status()
        except Exception:
            result["researchRecorder"] = {"enabled": False, "error": "status unavailable"}
        return result

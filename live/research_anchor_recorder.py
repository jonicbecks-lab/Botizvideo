from __future__ import annotations

from copy import deepcopy
from typing import Any

from .research_recorder import GalkaResearchRecorder, ResearchSession


class AnchoredResearchSession(ResearchSession):
    """Research session that starts full capture when a manual GALKA shape exists.

    Legacy/non-annotated campaigns keep the old armed-until-cross behavior. A
    manual structure means the user has explicitly marked the setup before order
    placement, so trades/L2/features are useful from that moment onward.
    """

    def _load_or_create_metadata(self, campaign: dict[str, Any]) -> dict[str, Any]:
        metadata = super()._load_or_create_metadata(campaign)
        setup = campaign.get("researchSetup")
        if not isinstance(setup, dict) or not setup.get("lockedForCampaign"):
            return metadata

        metadata["galkaStructure"] = deepcopy(setup)
        metadata["captureFromPlacement"] = True
        metadata["recordingStartBasis"] = "manual_galka_structure_locked"
        if not metadata.get("recordingStartedAt"):
            metadata["recordingStartedAt"] = campaign.get("createdAt")
        if metadata.get("status") == "armed":
            metadata["status"] = "recording"
        return metadata

    def mark_crossed_below(self, exchange_ms: int, receive_ns: int, price: float, basis: str) -> bool:
        started_at = self.metadata.get("recordingStartedAt")
        start_basis = self.metadata.get("recordingStartBasis")
        was_recording = bool(started_at)
        changed = super().mark_crossed_below(exchange_ms, receive_ns, price, basis)
        if changed and was_recording:
            with self._state_lock:
                self.metadata["recordingStartedAt"] = started_at
                self.metadata["recordingStartBasis"] = start_basis or "manual_galka_structure_locked"
                if isinstance(self.metadata.get("crossedBelowGalka"), dict):
                    self.metadata["crossedBelowGalka"]["captureAlreadyActive"] = True
            self.enqueue("metadata", None)
        return changed


class AnchoredGalkaResearchRecorder(GalkaResearchRecorder):
    """Drop-in recorder manager using AnchoredResearchSession for new campaigns."""

    def arm_campaign(self, campaign: dict[str, Any]) -> None:
        if not self.enabled:
            return
        campaign_id = str(campaign.get("id") or "")
        coin = str(campaign.get("coin") or "").upper()
        if not campaign_id or not coin:
            return
        with self._lock:
            session = self._sessions.get(campaign_id)
            if session is None:
                session = AnchoredResearchSession(self.settings, campaign)
                self._sessions[campaign_id] = session
                self._coin_to_campaign[coin] = campaign_id
                session.start()
            else:
                session.update_campaign(campaign, "restored_from_state")
            mode = "full" if session.recording and not session.campaign_completed_ms else "armed"
        self._stream.set_coin_mode(coin, mode)

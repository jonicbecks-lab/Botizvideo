from __future__ import annotations

import math
import time
from copy import deepcopy
from typing import Any

from .engine import ACTIVE_STATUSES
from .hyperliquid_safe_compat import SafeCompatibleGalkaLiveEngine
from .research_recorder import GalkaResearchRecorder


ALLOWED_STRUCTURE_INTERVALS = {"1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d"}
MAX_STRUCTURE_BARS = 240
MAX_CONTEXT_BARS = 40


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _positive_int(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0


def _bar(row: Any) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    time_ms = _positive_int(row.get("timeMs"))
    open_price = _finite(row.get("open"))
    high = _finite(row.get("high"))
    low = _finite(row.get("low"))
    close = _finite(row.get("close"))
    volume = max(0.0, _finite(row.get("volume")))
    if not time_ms or min(open_price, high, low, close) <= 0:
        return None
    return {
        "timeMs": time_ms,
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


def _bars(rows: Any, limit: int) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    result: list[dict[str, Any]] = []
    seen: set[int] = set()
    for source in rows[-limit:]:
        item = _bar(source)
        if item is None or item["timeMs"] in seen:
            continue
        seen.add(item["timeMs"])
        result.append(item)
    result.sort(key=lambda item: item["timeMs"])
    return result


class ResearchCompatibleGalkaLiveEngine(SafeCompatibleGalkaLiveEngine):
    """Safe LIVE engine with an isolated, best-effort research sidecar.

    The recorder never places/cancels orders and is not consulted by any trading
    decision. A manual chart structure is immutable campaign metadata: it explains
    what the user saw when choosing GALKA but never changes execution logic.
    """

    def __init__(self, config: Any, gateway: Any):
        self.research_recorder = GalkaResearchRecorder(config)
        self._pending_research_setup: dict[str, dict[str, Any]] = {}
        super().__init__(config, gateway)

    @staticmethod
    def _structure_summary(setup: dict[str, Any]) -> dict[str, Any]:
        bars = list(setup.get("structureBars") or [])
        if not bars:
            return {"barCount": 0}
        galka = _finite(setup.get("galkaLevel"))
        first = bars[0]
        last = bars[-1]
        highest = max(_finite(row.get("high")) for row in bars)
        lowest = min(_finite(row.get("low"), float("inf")) for row in bars)
        up = sum(1 for row in bars if _finite(row.get("close")) >= _finite(row.get("open")))
        volume = sum(max(0.0, _finite(row.get("volume"))) for row in bars)
        anchor = setup.get("anchorCandle") or first
        anchor_low = _finite(anchor.get("low")) if isinstance(anchor, dict) else 0.0
        anchor_close = _finite(anchor.get("close")) if isinstance(anchor, dict) else 0.0
        return {
            "barCount": int(setup.get("fullStructureBarCount") or len(bars)),
            "capturedBarCount": len(bars),
            "upBars": up,
            "downBars": len(bars) - up,
            "highestHigh": highest,
            "lowestLow": lowest if math.isfinite(lowest) else None,
            "totalCandleVolume": volume,
            "netMovePct": ((last["close"] / first["open"] - 1.0) * 100.0) if first["open"] else None,
            "galkaVsAnchorLowPct": ((galka / anchor_low - 1.0) * 100.0) if galka > 0 and anchor_low > 0 else None,
            "galkaVsAnchorClosePct": ((galka / anchor_close - 1.0) * 100.0) if galka > 0 and anchor_close > 0 else None,
        }

    def _normalize_research_setup(
        self,
        coin: str,
        galka_price: float,
        setup: Any,
    ) -> dict[str, Any] | None:
        if not isinstance(setup, dict):
            return None
        if str(setup.get("selectionMethod") or "") != "manual_crosshair_structure_v1":
            return None

        timeframe = str(setup.get("timeframe") or "5m")
        if timeframe not in ALLOWED_STRUCTURE_INTERVALS:
            timeframe = "5m"
        anchor_ms = _positive_int(setup.get("anchorTimeMs"))
        end_ms = _positive_int(setup.get("structureEndTimeMs"))
        if not anchor_ms or not end_ms:
            return None
        if end_ms < anchor_ms:
            anchor_ms, end_ms = end_ms, anchor_ms

        structure_bars = _bars(setup.get("structureBars"), MAX_STRUCTURE_BARS)
        pre_context = _bars(setup.get("preContextBars"), MAX_CONTEXT_BARS)
        post_context = _bars(setup.get("postContextBarsAtPlacement"), MAX_CONTEXT_BARS)
        anchor_candle = _bar(setup.get("anchorCandle"))
        end_candle = _bar(setup.get("structureEndCandle"))

        normalized = {
            "schemaVersion": 1,
            "selectionMethod": "manual_crosshair_structure_v1",
            "symbol": coin,
            "timeframe": timeframe,
            "galkaLevel": float(galka_price),
            "anchorTimeMs": anchor_ms,
            "structureEndTimeMs": end_ms,
            "selectedAtMs": _positive_int(setup.get("selectedAtMs")),
            "draftStartedAtMs": _positive_int(setup.get("draftStartedAtMs")),
            "serverReceivedAtMs": int(time.time() * 1000),
            "fullStructureBarCount": max(
                len(structure_bars),
                _positive_int(setup.get("fullStructureBarCount")),
            ),
            "structureBarsTruncated": bool(setup.get("structureBarsTruncated")),
            "anchorCandle": anchor_candle,
            "structureEndCandle": end_candle,
            "preContextBars": pre_context,
            "structureBars": structure_bars,
            "postContextBarsAtPlacement": post_context,
            "lockedForCampaign": True,
            "researchOnly": True,
        }
        normalized["derived"] = self._structure_summary(normalized)
        return normalized

    def start(self) -> None:
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
        with self.lock:
            setup = deepcopy(self._pending_research_setup.get(coin))
        if setup:
            campaign["researchSetup"] = setup
        try:
            # Session construction is local-only; actual file writes happen on
            # the recorder worker. With manual structure metadata the recorder
            # starts full market capture from placement instead of waiting for a
            # cross below GALKA.
            self.research_recorder.arm_campaign(campaign)
        except Exception:
            pass
        return campaign

    def create_campaign(
        self,
        coin: str,
        galka_price: float,
        confirmation: str,
        research_setup: Any = None,
    ) -> dict[str, Any]:
        normalized_coin = self._coin(coin)
        setup = self._normalize_research_setup(normalized_coin, galka_price, research_setup)
        if setup:
            with self.lock:
                self._pending_research_setup[normalized_coin] = setup
        try:
            result = super().create_campaign(normalized_coin, galka_price, confirmation)
        finally:
            with self.lock:
                self._pending_research_setup.pop(normalized_coin, None)

        try:
            self.research_recorder.on_campaign_snapshot(result, "created_and_orders_confirmed")
            if setup:
                self.research_recorder.on_galka_event(
                    "research",
                    "manual GALKA structure locked",
                    {
                        "campaignId": result.get("id"),
                        "event": "galka_structure_locked",
                        "anchorTimeMs": setup.get("anchorTimeMs"),
                        "structureEndTimeMs": setup.get("structureEndTimeMs"),
                        "timeframe": setup.get("timeframe"),
                        "galkaLevel": setup.get("galkaLevel"),
                    },
                    result,
                )
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

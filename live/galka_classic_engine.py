from __future__ import annotations

import math
from copy import deepcopy
from typing import Any

from .engine import RECOVERY_STATUS, LiveEngineError
from .hyperliquid_compat import CompatibleGalkaLiveEngine, CompatibleHyperliquidGateway
from .live_ladder import estimated_target_pnl, estimated_target_pnl_mixed, weighted_average


SIZING_POLICY = "max_available_whole_dollars_with_fee_reserve_v1"
TECHNICAL_BUFFER_USD = 0.05
MIN_CLASSIC_NOTIONAL = 80.0  # 8 live orders x Hyperliquid $10 minimum.
_EPSILON = 1e-9


class GalkaClassicGateway(CompatibleHyperliquidGateway):
    """Classic lower-only GALKA gateway."""


class GalkaClassicEngine(CompatibleGalkaLiveEngine):
    """Classic 8-level GALKA using the maximum safely available margin.

    Entries are only below GALKA. Any owned GALKA take-profit that returns the
    real position to flat ends the campaign; L1 is never rearmed automatically.

    Sizing uses the lower of account commitment capacity and venue withdrawable
    collateral, rounds the campaign budget down to whole margin dollars, and
    leaves enough cash for estimated maker entry fees plus a small technical
    cushion. Existing campaigns are reserved before sizing a new one.
    """

    @staticmethod
    def _finite(value: Any, default: float | None = None) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return default
        return number if math.isfinite(number) else default

    @staticmethod
    def _available_margin(
        account_value: float,
        withdrawable: float | None,
        reserved_margin: float,
    ) -> float:
        commitment_capacity = max(0.0, account_value - max(0.0, reserved_margin))
        if withdrawable is None:
            return commitment_capacity
        return min(commitment_capacity, max(0.0, withdrawable))

    def preview(self, coin: str, galka_price: float) -> dict[str, Any]:
        """Build the 8-level ladder from the maximum safe whole-dollar margin."""
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
        account_value = self._finite(account.get("accountValue"), 0.0) or 0.0
        if account_value <= 0:
            raise LiveEngineError("Hyperliquid не вернул положительный капитал для расчёта GALKA")

        withdrawable_raw = account.get("withdrawable")
        withdrawable = self._finite(withdrawable_raw) if withdrawable_raw is not None else None

        with self.lock:
            reserved_margin = sum(
                float(active.get("actualNotional") or active.get("requestedNotional") or 0)
                / max(1, int(active.get("leverage") or self.config.leverage))
                for active in self._active_campaigns_locked()
            )

        available_margin = self._available_margin(account_value, withdrawable, reserved_margin)
        whole_dollar_ceiling = int(math.floor(available_margin + _EPSILON))
        leverage = max(1, int(self.config.leverage))
        minimum_whole_dollars = int(math.ceil(MIN_CLASSIC_NOTIONAL / leverage))

        if whole_dollar_ceiling < minimum_whole_dollars:
            raise LiveEngineError(
                f"Недостаточно свободной маржи для 8 уровней GALKA: доступно ${available_margin:.2f}; "
                f"нужно минимум около ${minimum_whole_dollars:.0f} маржи."
            )

        selected: dict[str, Any] | None = None
        last_ladder_error: Exception | None = None

        # Start from every available whole dollar. Step down only when rounding
        # plus estimated entry fees would leave too little collateral.
        for target_margin_dollars in range(
            whole_dollar_ceiling,
            minimum_whole_dollars - 1,
            -1,
        ):
            requested_notional = float(target_margin_dollars * leverage)
            try:
                levels = self.gateway.preview_ladder(normalized, price, requested_notional)
            except ValueError as exc:
                last_ladder_error = exc
                continue

            actual_notional = float(sum(level.notional for level in levels))
            actual_margin = actual_notional / leverage
            estimated_entry_fees = actual_notional * float(self.config.maker_fee_rate)
            technical_reserve = estimated_entry_fees + TECHNICAL_BUFFER_USD
            cash_left_after_margin = max(0.0, available_margin - actual_margin)
            if cash_left_after_margin + _EPSILON < technical_reserve:
                continue

            selected = {
                "levels": levels,
                "requestedNotional": requested_notional,
                "actualNotional": actual_notional,
                "requiredMargin": actual_margin,
                "targetMargin": float(target_margin_dollars),
                "estimatedEntryFeeReserve": estimated_entry_fees,
                "technicalBufferUsd": TECHNICAL_BUFFER_USD,
                "technicalReserveRequired": technical_reserve,
                "cashLeftAfterMargin": cash_left_after_margin,
                "wholeDollarCeiling": float(whole_dollar_ceiling),
                "wholeDollarStepDown": whole_dollar_ceiling - target_margin_dollars,
            }
            break

        if selected is None:
            detail = f": {last_ladder_error}" if last_ladder_error else ""
            raise LiveEngineError(
                "Не удалось подобрать максимальную безопасную маржу с резервом на комиссии" + detail
            )

        levels = selected.pop("levels")
        actual_notional = float(selected["actualNotional"])
        return {
            "coin": normalized,
            "galkaPrice": price,
            "currentPrice": mid,
            "levels": [level.to_dict() for level in levels],
            "requestedNotional": selected["requestedNotional"],
            "actualNotional": actual_notional,
            "requiredMargin": float(selected["requiredMargin"]),
            "leverage": leverage,
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
            "withdrawable": withdrawable,
            "reservedMargin": reserved_margin,
            "availableMargin": available_margin,
            **selected,
            "sizingPolicy": SIZING_POLICY,
            "autoSizedFromEquity": True,
            "liveEnabled": self.config.live_enabled,
        }

    def _new_campaign(
        self,
        campaign_id: str,
        coin: str,
        galka_price: float,
        preview: dict[str, Any],
        levels: list[Any],
    ) -> dict[str, Any]:
        campaign = super()._new_campaign(campaign_id, coin, galka_price, preview, levels)
        campaign.pop("targetMarginFraction", None)
        campaign["autoSizedFromEquity"] = True
        for key in (
            "sizingPolicy",
            "targetMargin",
            "wholeDollarCeiling",
            "wholeDollarStepDown",
            "availableMargin",
            "reservedMargin",
            "estimatedEntryFeeReserve",
            "technicalBufferUsd",
            "technicalReserveRequired",
            "cashLeftAfterMargin",
        ):
            if key in preview:
                campaign[key] = deepcopy(preview[key])
        return campaign

    def _create_campaign_fast(
        self,
        coin: str,
        galka_price: float,
        confirmation: str,
    ) -> dict[str, Any]:
        """Treat the historical fraction setting only as a 100% hard ceiling.

        preview() has already reserved active campaigns, respected withdrawable
        collateral and left the fee cushion. The inherited create path has an old
        aggregate percentage guard, so raise that guard to 100% only while this
        serialized create operation runs, then restore the configured value.
        """
        previous_fraction = float(self.config.max_margin_fraction)
        object.__setattr__(self.config, "max_margin_fraction", 1.0)
        try:
            return super()._create_campaign_fast(coin, galka_price, confirmation)
        finally:
            object.__setattr__(self.config, "max_margin_fraction", previous_fraction)

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

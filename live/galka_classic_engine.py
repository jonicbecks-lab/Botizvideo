from __future__ import annotations

import math
import time
from copy import deepcopy
from typing import Any

from .engine import RECOVERY_STATUS, LiveEngineError, new_cloid, now_iso
from .hyperliquid_compat import CompatibleGalkaLiveEngine, CompatibleHyperliquidGateway
from .live_ladder import (
    estimated_target_pnl,
    estimated_target_pnl_mixed,
    round_perp_price,
    weighted_average,
)


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

    def _migrate_campaign(self, campaign: dict[str, Any]) -> None:
        super()._migrate_campaign(campaign)
        campaign.setdefault("originalGalkaPrice", campaign.get("galkaPrice"))
        campaign.setdefault("manualExitActive", False)
        campaign.setdefault("manualExitPrice", None)
        campaign.setdefault("manualExitOid", None)
        campaign.setdefault("manualExitCloid", None)

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
        campaign["originalGalkaPrice"] = float(galka_price)
        campaign["manualExitActive"] = False
        campaign["manualExitPrice"] = None
        campaign["manualExitOid"] = None
        campaign["manualExitCloid"] = None
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

    def _expected_target_price(self, campaign: dict[str, Any], order: dict[str, Any]) -> float:
        oid = int(order.get("oid") or 0)
        cloid = str(order.get("cloid") or "")
        manual_oid = int(campaign.get("manualExitOid") or 0)
        manual_cloid = str(campaign.get("manualExitCloid") or "")
        is_manual = bool(campaign.get("manualExitActive")) and (
            (manual_oid > 0 and oid == manual_oid)
            or (manual_cloid and cloid == manual_cloid)
        )
        source_price = (
            float(campaign.get("manualExitPrice") or 0)
            if is_manual
            else float(campaign.get("originalGalkaPrice") or campaign.get("galkaPrice") or 0)
        )
        return round_perp_price(source_price, self.gateway.sz_decimals(campaign["coin"]))

    def _is_galka_target(self, campaign: dict[str, Any], order: dict[str, Any]) -> bool:
        """Validate against the actual exchange-rounded price, not raw chart decimals.

        Manual near-market exits are intentional owned reduce-only targets and are
        validated against their own price without ever changing the original GALKA.
        """
        if not order.get("reduceOnly") or order.get("side") != "A":
            return False
        expected = self._expected_target_price(campaign, order)
        actual = float(order.get("triggerPrice") or order.get("price") or 0)
        return abs(actual - expected) <= max(1e-9, abs(expected) * 1e-10)

    def _ensure_target_coverage(
        self,
        campaign: dict[str, Any],
        open_orders: list[dict[str, Any]],
        actual_size: float,
    ) -> None:
        """Repair stale classic targets instead of entering an endless sync-error loop."""
        try:
            return super()._ensure_target_coverage(campaign, open_orders, actual_size)
        except LiveEngineError as exc:
            if campaign.get("manualExitActive") or "Owned target orders have wrong parameters" not in str(exc):
                raise

            with self.lock:
                self._register_delayed_orders(campaign, open_orders)
                malformed = [
                    int(row.get("oid") or 0)
                    for row in open_orders
                    if self._target_owner(campaign, row) is not None
                    and not self._is_galka_target(campaign, row)
                    and int(row.get("oid") or 0) > 0
                ]
            if not malformed:
                raise

            self._cancel_specific_and_verify(campaign["coin"], malformed)
            with self.lock:
                if int(campaign.get("fallbackTargetOid") or 0) in malformed:
                    campaign["fallbackTargetOid"] = None
                    campaign["fallbackTargetCloid"] = None
                for level in campaign.get("levels", []):
                    if int(level.get("tpOid") or 0) in malformed:
                        level["tpOid"] = None
                self._event_locked(
                    "risk",
                    f"{campaign['coin']}: некорректный owned target снят и будет восстановлен на исходной GALKA",
                    campaignId=campaign["id"],
                    oids=malformed,
                    galkaPrice=campaign.get("originalGalkaPrice") or campaign.get("galkaPrice"),
                )
                self._save_locked()

            fresh_orders = self.gateway.fresh_open_orders(campaign["coin"])
            return super()._ensure_target_coverage(campaign, fresh_orders, actual_size)

    def close_near_market(self, coin: str, confirmation: str) -> dict[str, Any]:
        """Close with a post-only maker order without rewriting the campaign GALKA."""
        normalized = self._coin(coin)
        self._require_live_writes()
        if confirmation != "CLOSE_NEAR_MARKET":
            raise LiveEngineError("Не подтверждено закрытие рядом с рынком")

        with self.action_lock:
            with self.lock:
                campaign = self._active_campaign_locked(normalized)
                if not campaign:
                    raise LiveEngineError(f"Для {normalized} нет активной GALKA")
                campaign["autoRearmBlocked"] = True
                campaign["abortAfterClose"] = True
                campaign["manualExitActive"] = True
                campaign["status"] = "closing"
                campaign["updatedAt"] = now_iso()
                self._save_locked()

            account = self.gateway.fresh_account_state()
            position_size = self._position_size(account, normalized)
            tolerance = self._size_tolerance(normalized)
            if position_size <= tolerance:
                self._sync_campaign(campaign)
                with self.lock:
                    return {
                        "coin": normalized,
                        "price": None,
                        "size": 0.0,
                        "oid": None,
                        "status": campaign.get("status"),
                        "alreadyFlat": True,
                    }

            open_orders = self.gateway.fresh_open_orders(normalized)
            self._cancel_owned_orders(campaign, open_orders=open_orders)

            account = self.gateway.fresh_account_state()
            position_size = self._position_size(account, normalized)
            if position_size <= tolerance:
                self._sync_campaign(campaign)
                with self.lock:
                    return {
                        "coin": normalized,
                        "price": None,
                        "size": 0.0,
                        "oid": None,
                        "status": campaign.get("status"),
                        "alreadyFlat": True,
                    }

            cloid = new_cloid()
            step = self._NEAR_MARKET_STEPS[normalized]
            placed = None
            exit_price = 0.0
            last_error: Exception | None = None
            for multiplier in (1, 2, 5):
                mid = float(self.gateway.mids().get(normalized) or 0)
                if mid <= 0:
                    raise LiveEngineError(f"Нет свежей рыночной цены {normalized}")
                exit_price = round_perp_price(
                    mid + step * multiplier, self.gateway.sz_decimals(normalized)
                )
                if exit_price <= mid:
                    exit_price = round_perp_price(
                        mid + step * (multiplier + 1), self.gateway.sz_decimals(normalized)
                    )
                try:
                    placed = self.gateway.place_post_only_reduce_sell(
                        normalized, position_size, exit_price, cloid
                    )
                    break
                except Exception as exc:
                    last_error = exc
                    time.sleep(0.15)
            if placed is None:
                self._enter_recovery(
                    campaign,
                    f"Не удалось выставить maker-выход рядом с рынком: {last_error}",
                    position_size,
                    self.gateway.fresh_open_orders(normalized),
                )
                raise LiveEngineError("Выход рядом с рынком не выставлен; включён recovery")

            with self.lock:
                # galkaPrice/originalGalkaPrice are intentionally immutable here.
                campaign["manualExitPrice"] = exit_price
                campaign["manualExitOid"] = placed.oid
                campaign["manualExitCloid"] = cloid
                campaign["fallbackTargetOid"] = placed.oid
                campaign["fallbackTargetCloid"] = cloid
                campaign.setdefault("targetOidMap", {})[str(placed.oid)] = 0
                campaign.setdefault("targetCloidMap", {})[cloid] = 0
                campaign["status"] = "closing"
                campaign["updatedAt"] = now_iso()
                self._event_locked(
                    "live",
                    f"{normalized}: входы и старые TP сняты; весь объём выставлен на продажу по {exit_price:g}; исходная GALKA сохранена",
                    campaignId=campaign["id"],
                    price=exit_price,
                    originalGalkaPrice=campaign.get("originalGalkaPrice") or campaign.get("galkaPrice"),
                    size=position_size,
                    oid=placed.oid,
                )
                self._save_locked()
                return {
                    "coin": normalized,
                    "price": exit_price,
                    "size": position_size,
                    "oid": placed.oid,
                    "status": "closing",
                }

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
            if campaign.get("manualExitActive"):
                message = (
                    f"{coin}: L{deepest} закрыта ручным maker-выходом "
                    f"{float(campaign.get('manualExitPrice') or 0):g}; исходная GALKA "
                    f"{float(campaign.get('originalGalkaPrice') or campaign.get('galkaPrice') or 0):g} сохранена"
                )
            else:
                message = f"{coin}: L{deepest} закрыта на GALKA; кампания завершена без rearm"
            self._event_locked(
                "live",
                message,
                campaignId=campaign["id"],
                deepest=deepest,
                pnl=net_cycle,
                originalGalkaPrice=campaign.get("originalGalkaPrice") or campaign.get("galkaPrice"),
                manualExitPrice=campaign.get("manualExitPrice"),
            )
            self._save_locked()

    @staticmethod
    def _now_iso_compat() -> str:
        return now_iso()

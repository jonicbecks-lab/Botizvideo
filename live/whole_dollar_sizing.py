from __future__ import annotations

import math
from copy import deepcopy
from typing import Any

from .engine import LiveEngineError
from .hyperliquid_safe_compat import SafeCompatibleGalkaLiveEngine
from .live_ladder import estimated_target_pnl, estimated_target_pnl_mixed, weighted_average


SIZING_POLICY = "whole_dollars_with_entry_fee_reserve_v1"
TECHNICAL_BUFFER_USD = 0.05
_MIN_NOTIONAL = 40.0
_EPSILON = 1e-9

_ORIGINAL_NEW_CAMPAIGN = SafeCompatibleGalkaLiveEngine._new_campaign
_ORIGINAL_CREATE_FAST = SafeCompatibleGalkaLiveEngine._create_campaign_fast
_INSTALLED = False


def _finite(value: Any, default: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _available_margin(account_value: float, withdrawable: float | None, reserved_margin: float) -> float:
    """Return conservative capital that can still be committed to a new campaign.

    ``account_value - reserved_margin`` protects against all currently active GALKA
    campaigns filling completely. ``withdrawable`` additionally respects collateral
    that Hyperliquid itself says is unavailable. Taking the lower value avoids
    double-spending without relying on open limit orders to reserve venue margin.
    """
    commitment_capacity = max(0.0, account_value - max(0.0, reserved_margin))
    if withdrawable is None:
        return commitment_capacity
    return min(commitment_capacity, max(0.0, withdrawable))


def _preview(self: SafeCompatibleGalkaLiveEngine, coin: str, galka_price: float) -> dict[str, Any]:
    """Use maximum safe whole-dollar margin instead of a fixed percentage buffer.

    The preferred budget is ``floor(available_margin)`` dollars. If the cents left
    after the rounded ladder are not enough to pay the maker entry fees plus a tiny
    technical cushion, the budget is reduced by exactly one dollar and retried.
    Research, ladder weights, depths and exchange placement logic are unchanged.
    """
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
    account_value = _finite(account.get("accountValue"), 0.0) or 0.0
    if account_value <= 0:
        raise LiveEngineError("Hyperliquid не вернул положительный капитал для расчёта GALKA")

    withdrawable_raw = account.get("withdrawable")
    withdrawable = _finite(withdrawable_raw) if withdrawable_raw is not None else None

    with self.lock:
        reserved_margin = sum(
            float(active.get("actualNotional") or active.get("requestedNotional") or 0)
            / max(1, int(active.get("leverage") or self.config.leverage))
            for active in self._active_campaigns_locked()
        )

    available_margin = _available_margin(account_value, withdrawable, reserved_margin)
    whole_dollar_ceiling = int(math.floor(available_margin + _EPSILON))
    leverage = max(1, int(self.config.leverage))
    minimum_whole_dollars = int(math.ceil(_MIN_NOTIONAL / leverage))

    if whole_dollar_ceiling < minimum_whole_dollars:
        raise LiveEngineError(
            f"Недостаточно свободной маржи для новой GALKA: доступно ${available_margin:.2f}; "
            f"для 4 активных лимиток нужно минимум около ${minimum_whole_dollars:.0f} маржи."
        )

    selected: dict[str, Any] | None = None
    last_ladder_error: Exception | None = None

    for target_margin_dollars in range(whole_dollar_ceiling, minimum_whole_dollars - 1, -1):
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
            "wholeDollarStepDown": whole_dollar_ceiling - target_margin_dollars,
        }
        break

    if selected is None:
        detail = f": {last_ladder_error}" if last_ladder_error else ""
        raise LiveEngineError(
            "Не удалось подобрать безопасный целый доллар маржи с резервом на входные комиссии" + detail
        )

    levels = selected.pop("levels")
    actual_notional = float(selected["actualNotional"])
    required_margin = float(selected["requiredMargin"])
    legacy_fraction = _finite(getattr(self.config, "max_margin_fraction", None))

    return {
        "coin": normalized,
        "galkaPrice": price,
        "currentPrice": mid,
        "levels": [level.to_dict() for level in levels],
        "requestedNotional": selected["requestedNotional"],
        "actualNotional": actual_notional,
        "requiredMargin": required_margin,
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
        "wholeDollarCeiling": float(whole_dollar_ceiling),
        **selected,
        "sizingPolicy": SIZING_POLICY,
        "autoSizedFromEquity": True,
        "legacyMaxMarginFraction": legacy_fraction,
        "liveEnabled": self.config.live_enabled,
    }


def _new_campaign(
    self: SafeCompatibleGalkaLiveEngine,
    campaign_id: str,
    coin: str,
    galka_price: float,
    preview: dict[str, Any],
    levels: list[Any],
) -> dict[str, Any]:
    campaign = _ORIGINAL_NEW_CAMPAIGN(self, campaign_id, coin, galka_price, preview, levels)
    campaign.pop("targetMarginFraction", None)
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
        "legacyMaxMarginFraction",
    ):
        if key in preview:
            campaign[key] = deepcopy(preview[key])
    return campaign


def _create_campaign_fast(
    self: SafeCompatibleGalkaLiveEngine,
    coin: str,
    galka_price: float,
    confirmation: str,
) -> dict[str, Any]:
    """Keep the old aggregate guard as a 100% hard ceiling, not a 99% sizing policy.

    ``CompatibleGalkaLiveEngine`` performs a second aggregate-risk check after the
    preview. Its historical source is ``HL_MAX_MARGIN_FRACTION``. The new preview
    has already reserved other campaigns, respected withdrawable collateral and
    left the entry-fee cushion, so this legacy check is temporarily raised to 100%
    only for the serialized create operation. The configured value is restored in
    ``finally`` and remains available as legacy configuration/rollback metadata.
    """
    previous_fraction = float(self.config.max_margin_fraction)
    object.__setattr__(self.config, "max_margin_fraction", 1.0)
    try:
        return _ORIGINAL_CREATE_FAST(self, coin, galka_price, confirmation)
    finally:
        object.__setattr__(self.config, "max_margin_fraction", previous_fraction)


def install() -> None:
    """Install the production sizing policy without altering ladder/execution logic."""
    global _INSTALLED
    if _INSTALLED:
        return
    SafeCompatibleGalkaLiveEngine.preview = _preview
    SafeCompatibleGalkaLiveEngine._new_campaign = _new_campaign
    SafeCompatibleGalkaLiveEngine._create_campaign_fast = _create_campaign_fast
    _INSTALLED = True

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable

from .live_ladder import MIN_ORDER_NOTIONAL, round_perp_price, round_size_down

LOWER_OFFSETS_PCT = (-2.0, -4.0, -6.0, -8.0)
UPPER_SHARE = 1.0 / 3.0
LOWER_SHARE = 2.0 / 3.0
MAX_UPPER_OFFSET_PCT = 5.0
DEFAULT_TARGET_ROE = 0.105
LIQUIDATION_BUFFER = 0.05
MAX_CAMPAIGN_MARGIN = 100.0


@dataclass(frozen=True)
class MemLevel:
    index: int
    basket: str
    offset_pct: float
    weight: float
    price: float
    margin: float
    notional: float
    size: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class LiquidationState:
    filled_levels: int
    last_level_index: int
    average_entry: float
    position_size: float
    position_notional_at_entry: float
    isolated_margin: float
    estimated_liquidation_price: float
    required_max_liquidation_price: float
    safe: bool

    def to_dict(self) -> dict:
        return asdict(self)


def _weights(count: int) -> list[float]:
    if count <= 0:
        return []
    return [1.0 + 0.5 * index for index in range(count)]


def _normalize_allocations(total_margin: float, count: int) -> list[float]:
    weights = _weights(count)
    total_weight = sum(weights)
    return [total_margin * weight / total_weight for weight in weights]


def _offset_pct(price: float, galka_price: float) -> float:
    return (price / galka_price - 1.0) * 100.0


def _build_levels(
    *,
    basket: str,
    prices: list[float],
    margin_budget: float,
    leverage: int,
    sz_decimals: int,
    galka_price: float,
    start_index: int,
) -> list[MemLevel]:
    ordered = sorted(prices, reverse=True)
    allocations = _normalize_allocations(margin_budget, len(ordered))
    weights = _weights(len(ordered))
    levels: list[MemLevel] = []

    for offset, (raw_price, margin, weight) in enumerate(zip(ordered, allocations, weights)):
        price = round_perp_price(raw_price, sz_decimals)
        target_notional = margin * leverage
        size = round_size_down(target_notional / price, sz_decimals)
        notional = price * size
        actual_margin = notional / leverage
        if notional + 1e-9 < float(MIN_ORDER_NOTIONAL):
            raise ValueError(
                f"{basket} level at {price:g} is only ${notional:.2f} notional; "
                f"Hyperliquid minimum is ${float(MIN_ORDER_NOTIONAL):.2f}"
            )
        levels.append(
            MemLevel(
                index=start_index + offset,
                basket=basket,
                offset_pct=_offset_pct(price, galka_price),
                weight=weight,
                price=price,
                margin=actual_margin,
                notional=notional,
                size=size,
            )
        )
    return levels


def weighted_average(levels: Iterable[MemLevel]) -> float:
    rows = list(levels)
    size = sum(level.size for level in rows)
    if size <= 0:
        return 0.0
    return sum(level.price * level.size for level in rows) / size


def estimate_isolated_long_liquidation(
    *,
    entry_cost: float,
    position_size: float,
    isolated_margin: float,
    max_leverage: int,
) -> float:
    """Tier-0 Hyperliquid isolated-long liquidation estimate.

    At our v1 campaign size (<= roughly $500 notional), margin tiers do not
    apply. Hyperliquid maintenance margin is half the initial margin at the
    venue maximum leverage, so mmr = 1 / (2 * max_leverage).
    """
    if position_size <= 0 or entry_cost <= 0 or isolated_margin <= 0:
        raise ValueError("position inputs must be positive")
    if max_leverage <= 0:
        raise ValueError("max_leverage must be positive")
    maintenance_rate = 1.0 / (2.0 * max_leverage)
    denominator = position_size * (1.0 - maintenance_rate)
    liquidation = (entry_cost - isolated_margin) / denominator
    return max(0.0, liquidation)


def build_mem_plan(
    *,
    galka_price: float,
    upper_prices: Iterable[float],
    campaign_margin: float,
    leverage: int,
    max_leverage: int,
    sz_decimals: int,
    target_roe: float = DEFAULT_TARGET_ROE,
) -> dict:
    galka_price = float(galka_price)
    campaign_margin = float(campaign_margin)
    leverage = int(leverage)
    max_leverage = int(max_leverage)

    if galka_price <= 0:
        raise ValueError("GALKA price must be positive")
    if campaign_margin <= 0 or campaign_margin > MAX_CAMPAIGN_MARGIN:
        raise ValueError(f"campaign margin must be between $0 and ${MAX_CAMPAIGN_MARGIN:.0f}")
    if leverage < 1 or leverage > max_leverage:
        raise ValueError(f"leverage must be between 1x and market max {max_leverage}x")
    if not 0 < target_roe <= 1:
        raise ValueError("target_roe must be between 0 and 1")

    upper = [float(value) for value in upper_prices]
    if not upper:
        raise ValueError("at least one upper-basket entry is required")
    if any(value <= 0 for value in upper):
        raise ValueError("upper entry prices must be positive")
    if len({round(value, 12) for value in upper}) != len(upper):
        raise ValueError("upper entry prices must be unique")

    max_upper = galka_price * (1.0 + MAX_UPPER_OFFSET_PCT / 100.0)
    for value in upper:
        if value < galka_price * (1.0 - 1e-12) or value > max_upper * (1.0 + 1e-12):
            raise ValueError(
                f"upper entries must be between GALKA and +{MAX_UPPER_OFFSET_PCT:.0f}%"
            )

    lower_raw = [galka_price * (1.0 + pct / 100.0) for pct in LOWER_OFFSETS_PCT]
    upper_margin_budget = campaign_margin * UPPER_SHARE
    lower_margin_budget = campaign_margin * LOWER_SHARE

    upper_levels = _build_levels(
        basket="upper",
        prices=upper,
        margin_budget=upper_margin_budget,
        leverage=leverage,
        sz_decimals=sz_decimals,
        galka_price=galka_price,
        start_index=1,
    )
    lower_levels = _build_levels(
        basket="lower",
        prices=lower_raw,
        margin_budget=lower_margin_budget,
        leverage=leverage,
        sz_decimals=sz_decimals,
        galka_price=galka_price,
        start_index=len(upper_levels) + 1,
    )

    levels = upper_levels + lower_levels
    rounded_prices = [level.price for level in levels]
    if len(set(rounded_prices)) != len(rounded_prices):
        raise ValueError("two planned levels collapse to the same Hyperliquid price after rounding")

    deepest = lower_levels[-1].price
    required_max_liq = deepest * (1.0 - LIQUIDATION_BUFFER)
    liquidation_states: list[LiquidationState] = []
    entry_cost = 0.0
    position_size = 0.0
    isolated_margin = 0.0

    for count, level in enumerate(levels, start=1):
        entry_cost += level.price * level.size
        position_size += level.size
        isolated_margin += level.margin
        average = entry_cost / position_size
        liq = estimate_isolated_long_liquidation(
            entry_cost=entry_cost,
            position_size=position_size,
            isolated_margin=isolated_margin,
            max_leverage=max_leverage,
        )
        liquidation_states.append(
            LiquidationState(
                filled_levels=count,
                last_level_index=level.index,
                average_entry=average,
                position_size=position_size,
                position_notional_at_entry=entry_cost,
                isolated_margin=isolated_margin,
                estimated_liquidation_price=liq,
                required_max_liquidation_price=required_max_liq,
                safe=liq <= required_max_liq + 1e-12,
            )
        )

    safe = all(item.safe for item in liquidation_states)
    upper_avg = weighted_average(upper_levels)
    upper_target_move = target_roe / leverage
    upper_tp = round_perp_price(upper_avg * (1.0 + upper_target_move), sz_decimals)
    lower_tp = round_perp_price(galka_price, sz_decimals)

    return {
        "galkaPrice": lower_tp,
        "campaignMargin": campaign_margin,
        "leverage": leverage,
        "maxLeverage": max_leverage,
        "targetRoe": target_roe,
        "upperTargetMovePct": upper_target_move * 100.0,
        "upperBudget": upper_margin_budget,
        "lowerBudget": lower_margin_budget,
        "actualMargin": sum(level.margin for level in levels),
        "actualNotional": sum(level.notional for level in levels),
        "upperAverage": upper_avg,
        "upperTakeProfit": upper_tp,
        "lowerTakeProfit": lower_tp,
        "deepestLowerPrice": deepest,
        "requiredMaxLiquidationPrice": required_max_liq,
        "safe": safe,
        "levels": [level.to_dict() for level in levels],
        "liquidationStates": [state.to_dict() for state in liquidation_states],
    }

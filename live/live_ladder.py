from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_DOWN
from math import floor, log10
from typing import Iterable

MANUAL_DEPTHS = (0.15, 0.30, 0.45, 0.60, 0.90, 1.20, 1.50, 2.00)
# LIVE capital allocation: L1 25%, L2 32%, L3 25%, L4 18%.
# L5-L8 remain reference depths on the chart and must not create exchange orders.
MANUAL_WEIGHTS = (0.25, 0.32, 0.25, 0.18, 0.0, 0.0, 0.0, 0.0)
MIN_ORDER_NOTIONAL = Decimal("10")


@dataclass(frozen=True)
class LadderLevel:
    index: int
    depth_pct: float
    weight: float
    price: float
    size: float
    notional: float

    def to_dict(self) -> dict:
        return asdict(self)


def _decimal(value: float | str | Decimal) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def round_perp_price(price: float, sz_decimals: int) -> float:
    """Round to Hyperliquid perp price constraints.

    Perp prices allow at most five significant figures and no more than
    6 - szDecimals decimal places. Integer prices are always allowed.
    """
    if price <= 0:
        raise ValueError("price must be positive")
    allowed_decimals = max(0, 6 - int(sz_decimals))
    magnitude = floor(log10(abs(price)))
    significant_decimals = 5 - magnitude - 1
    decimals = max(0, min(allowed_decimals, significant_decimals))
    quantum = Decimal("1").scaleb(-decimals)
    return float(_decimal(price).quantize(quantum))


def round_size_down(size: float, sz_decimals: int) -> float:
    if size <= 0:
        raise ValueError("size must be positive")
    quantum = Decimal("1").scaleb(-int(sz_decimals))
    rounded = _decimal(size).quantize(quantum, rounding=ROUND_DOWN)
    if rounded <= 0:
        raise ValueError("size rounds to zero")
    return float(rounded)


def _minimum_size(price: Decimal, sz_decimals: int) -> Decimal:
    quantum = Decimal("1").scaleb(-int(sz_decimals))
    units = (MIN_ORDER_NOTIONAL / price / quantum).to_integral_value(rounding=ROUND_CEILING)
    return units * quantum


def _allocate_targets(total: Decimal, raw: list[Decimal], minimums: list[Decimal]) -> list[Decimal]:
    if sum(minimums) > total:
        required = sum(minimums)
        raise ValueError(
            f"Active Hyperliquid orders require at least ${required:.2f} notional at current prices; "
            f"requested ${total:.2f}"
        )

    targets = [max(value, minimum) for value, minimum in zip(raw, minimums)]
    excess = sum(targets) - total
    while excess > Decimal("0.00000001"):
        capacities = [max(Decimal(0), target - minimum) for target, minimum in zip(targets, minimums)]
        total_capacity = sum(capacities)
        if total_capacity <= 0:
            raise ValueError("not enough allocation capacity to satisfy minimum order notionals")
        removed = Decimal(0)
        for index, capacity in enumerate(capacities):
            if capacity <= 0:
                continue
            deduction = min(capacity, excess * capacity / total_capacity)
            targets[index] -= deduction
            removed += deduction
        if removed <= 0:
            break
        excess -= removed
    return targets


def build_ladder(galka_price: float, total_notional: float, sz_decimals: int) -> list[LadderLevel]:
    if galka_price <= 0:
        raise ValueError("GALKA price must be positive")
    if total_notional <= 0:
        raise ValueError("total notional must be positive")
    if len(MANUAL_DEPTHS) != len(MANUAL_WEIGHTS):
        raise RuntimeError("manual ladder configuration mismatch")
    if abs(sum(MANUAL_WEIGHTS) - 1.0) > 1e-9:
        raise RuntimeError("manual ladder weights must sum to one")

    active_rows = [
        (index, depth_pct, weight)
        for index, (depth_pct, weight) in enumerate(zip(MANUAL_DEPTHS, MANUAL_WEIGHTS), start=1)
        if weight > 0
    ]
    if not active_rows:
        raise RuntimeError("manual ladder has no active order levels")

    prices = [
        _decimal(round_perp_price(galka_price * (1 - depth_pct / 100), sz_decimals))
        for _, depth_pct, _ in active_rows
    ]
    minimum_sizes = [_minimum_size(price, sz_decimals) for price in prices]
    minimum_notionals = [price * size for price, size in zip(prices, minimum_sizes)]
    total = _decimal(total_notional)
    raw_targets = [total * _decimal(weight) for _, _, weight in active_rows]
    targets = _allocate_targets(total, raw_targets, minimum_notionals)

    levels: list[LadderLevel] = []
    quantum = Decimal("1").scaleb(-int(sz_decimals))
    for (level_index, depth_pct, weight), price, target, minimum_size in zip(
        active_rows, prices, targets, minimum_sizes
    ):
        size = (target / price).quantize(quantum, rounding=ROUND_DOWN)
        size = max(size, minimum_size)
        actual_notional = price * size
        if actual_notional < MIN_ORDER_NOTIONAL:
            raise ValueError(f"L{level_index} notional {actual_notional:.4f} is below Hyperliquid minimum $10")
        levels.append(
            LadderLevel(
                index=level_index,
                depth_pct=depth_pct,
                weight=weight,
                price=float(price),
                size=float(size),
                notional=float(actual_notional),
            )
        )
    return levels


def weighted_average(levels: Iterable[LadderLevel]) -> float:
    rows = list(levels)
    total_size = sum(level.size for level in rows)
    if not total_size:
        return 0.0
    return sum(level.price * level.size for level in rows) / total_size


def estimated_target_pnl_mixed(
    levels: Iterable[LadderLevel],
    galka_price: float,
    entry_fee_rate: float,
    exit_fee_rate: float,
) -> float:
    rows = list(levels)
    qty = sum(level.size for level in rows)
    entry_notional = sum(level.price * level.size for level in rows)
    exit_notional = galka_price * qty
    gross = exit_notional - entry_notional
    fees = entry_notional * entry_fee_rate + exit_notional * exit_fee_rate
    return gross - fees


def estimated_target_pnl(levels: Iterable[LadderLevel], galka_price: float, maker_fee: float) -> float:
    return estimated_target_pnl_mixed(levels, galka_price, maker_fee, maker_fee)

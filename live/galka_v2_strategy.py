from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isfinite

from .live_ladder import MIN_ORDER_NOTIONAL, LadderLevel, round_perp_price, round_size_down

# GALKA V2 test campaign: fixed $100 margin at 10x = $1,000 notional.
V2_MARGIN_USD = 100.0
V2_LEVERAGE = 10
V2_TOTAL_NOTIONAL = V2_MARGIN_USD * V2_LEVERAGE

# Right-leg retracements. The fifth upper entry is the GALKA level itself.
FIB_RETRACEMENTS = (0.50, 0.618, 0.705, 0.786)
UPPER_LABELS = ("F0.50", "F0.618", "F0.705", "F0.786", "GALKA")
UPPER_INTERNAL_WEIGHTS = (0.10, 0.15, 0.20, 0.25, 0.30)

# Lower GALKA ladder. Deeper entries receive progressively more capital.
LOWER_DEPTHS = (0.15, 0.30, 0.45, 0.60)
LOWER_INTERNAL_WEIGHTS = (0.10, 0.20, 0.30, 0.40)

UPPER_MAX_SHARE = 0.40
UPPER_MIN_SHARE = 0.10
UPPER_SHARE_STEP = 0.005
FULL_PLAN_MIN_NET_AT_GALKA_USD = 0.10
UPPER_NET_ROE_TARGET = 0.01


@dataclass(frozen=True)
class V2Level:
    index: int
    label: str
    zone: str
    price: float
    size: float
    notional: float
    weight: float
    depth_pct: float
    retracement: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    def to_ladder_level(self) -> LadderLevel:
        return LadderLevel(
            index=self.index,
            depth_pct=self.depth_pct,
            weight=self.weight,
            price=self.price,
            size=self.size,
            notional=self.notional,
        )


@dataclass(frozen=True)
class V2Plan:
    galka_price: float
    right_high: float
    total_notional: float
    upper_share: float
    lower_share: float
    weighted_average: float
    full_fill_net_at_galka: float
    levels: tuple[V2Level, ...]

    def to_dict(self) -> dict:
        return {
            "galkaPrice": self.galka_price,
            "rightHigh": self.right_high,
            "totalNotional": self.total_notional,
            "upperShare": self.upper_share,
            "lowerShare": self.lower_share,
            "weightedAverage": self.weighted_average,
            "fullFillNetAtGalka": self.full_fill_net_at_galka,
            "levels": [level.to_dict() for level in self.levels],
        }


def _validate_price(value: float, label: str) -> float:
    number = float(value)
    if not isfinite(number) or number <= 0:
        raise ValueError(f"{label} must be a finite positive number")
    return number


def fibonacci_entry_prices(galka_price: float, right_high: float) -> tuple[float, ...]:
    """Return four right-leg retracement entries plus the GALKA entry."""
    galka = _validate_price(galka_price, "GALKA price")
    high = _validate_price(right_high, "right-leg high")
    if high <= galka:
        raise ValueError("right-leg high must be above GALKA")
    span = high - galka
    prices = tuple(high - retracement * span for retracement in FIB_RETRACEMENTS)
    return (*prices, galka)


def _actual_level(
    *,
    index: int,
    label: str,
    zone: str,
    raw_price: float,
    target_notional: float,
    total_notional: float,
    galka_price: float,
    sz_decimals: int,
    retracement: float | None = None,
) -> V2Level:
    price = round_perp_price(raw_price, sz_decimals)
    size = round_size_down(target_notional / price, sz_decimals)
    notional = price * size
    if notional + 1e-9 < float(MIN_ORDER_NOTIONAL):
        raise ValueError(
            f"{label} notional ${notional:.2f} is below Hyperliquid minimum ${float(MIN_ORDER_NOTIONAL):.2f}"
        )
    signed_depth = (galka_price - price) / galka_price * 100.0
    return V2Level(
        index=index,
        label=label,
        zone=zone,
        price=price,
        size=size,
        notional=notional,
        weight=target_notional / total_notional,
        depth_pct=signed_depth,
        retracement=retracement,
    )


def _build_for_share(
    galka_price: float,
    right_high: float,
    total_notional: float,
    sz_decimals: int,
    upper_share: float,
) -> tuple[V2Level, ...]:
    if not UPPER_MIN_SHARE - 1e-12 <= upper_share <= UPPER_MAX_SHARE + 1e-12:
        raise ValueError("upper share is outside the GALKA V2 safety range")
    lower_share = 1.0 - upper_share
    upper_prices = fibonacci_entry_prices(galka_price, right_high)
    levels: list[V2Level] = []

    retracements: tuple[float | None, ...] = (*FIB_RETRACEMENTS, 1.0)
    for offset, (label, raw_price, internal_weight, retracement) in enumerate(
        zip(UPPER_LABELS, upper_prices, UPPER_INTERNAL_WEIGHTS, retracements),
        start=1,
    ):
        target = total_notional * upper_share * internal_weight
        levels.append(
            _actual_level(
                index=offset,
                label=label,
                zone="upper",
                raw_price=raw_price,
                target_notional=target,
                total_notional=total_notional,
                galka_price=galka_price,
                sz_decimals=sz_decimals,
                retracement=retracement,
            )
        )

    for lower_offset, (depth, internal_weight) in enumerate(
        zip(LOWER_DEPTHS, LOWER_INTERNAL_WEIGHTS),
        start=1,
    ):
        raw_price = galka_price * (1.0 - depth / 100.0)
        target = total_notional * lower_share * internal_weight
        levels.append(
            _actual_level(
                index=5 + lower_offset,
                label=f"D{lower_offset}",
                zone="lower",
                raw_price=raw_price,
                target_notional=target,
                total_notional=total_notional,
                galka_price=galka_price,
                sz_decimals=sz_decimals,
            )
        )
    return tuple(levels)


def net_pnl_at_price(
    levels: tuple[V2Level, ...] | list[V2Level],
    exit_price: float,
    entry_fee_rate: float,
    exit_fee_rate: float,
) -> float:
    rows = list(levels)
    qty = sum(level.size for level in rows)
    entry_notional = sum(level.price * level.size for level in rows)
    exit_notional = float(exit_price) * qty
    return (
        exit_notional
        - entry_notional
        - entry_notional * float(entry_fee_rate)
        - exit_notional * float(exit_fee_rate)
    )


def weighted_average(levels: tuple[V2Level, ...] | list[V2Level]) -> float:
    rows = list(levels)
    qty = sum(level.size for level in rows)
    return sum(level.price * level.size for level in rows) / qty if qty else 0.0


def build_v2_plan(
    galka_price: float,
    right_high: float,
    sz_decimals: int,
    *,
    total_notional: float = V2_TOTAL_NOTIONAL,
    entry_fee_rate: float = 0.00015,
    exit_fee_rate: float = 0.00015,
    min_net_at_galka: float = FULL_PLAN_MIN_NET_AT_GALKA_USD,
) -> V2Plan:
    galka = _validate_price(galka_price, "GALKA price")
    high = _validate_price(right_high, "right-leg high")
    total = float(total_notional)
    if not isfinite(total) or total <= 0:
        raise ValueError("total notional must be positive")
    if abs(total - V2_TOTAL_NOTIONAL) > 1e-6:
        raise ValueError(f"GALKA V2 test notional is fixed at ${V2_TOTAL_NOTIONAL:.2f}")

    steps = int(round((UPPER_MAX_SHARE - UPPER_MIN_SHARE) / UPPER_SHARE_STEP))
    selected: tuple[float, tuple[V2Level, ...], float] | None = None
    for step in range(steps + 1):
        share = round(UPPER_MAX_SHARE - step * UPPER_SHARE_STEP, 6)
        levels = _build_for_share(galka, high, total, sz_decimals, share)
        net = net_pnl_at_price(levels, galka, entry_fee_rate, exit_fee_rate)
        if net + 1e-9 >= float(min_net_at_galka):
            selected = (share, levels, net)
            break
    if selected is None:
        raise ValueError(
            "Right leg is too tall for the V2 risk rule: even 10% upper allocation "
            "does not break even at GALKA after a full lower fill"
        )

    share, levels, net = selected
    return V2Plan(
        galka_price=galka,
        right_high=high,
        total_notional=total,
        upper_share=share,
        lower_share=1.0 - share,
        weighted_average=weighted_average(levels),
        full_fill_net_at_galka=net,
        levels=levels,
    )


def upper_take_profit_price(
    *,
    quantity: float,
    entry_notional: float,
    entry_fees_paid: float,
    leverage: int = V2_LEVERAGE,
    net_roe_target: float = UPPER_NET_ROE_TARGET,
    exit_fee_rate: float = 0.00015,
) -> float:
    qty = float(quantity)
    entry = float(entry_notional)
    fees = max(0.0, float(entry_fees_paid))
    if qty <= 0 or entry <= 0:
        raise ValueError("upper filled quantity and entry notional must be positive")
    if leverage <= 0:
        raise ValueError("leverage must be positive")
    if not 0 <= exit_fee_rate < 1:
        raise ValueError("exit fee rate is invalid")
    used_margin = entry / leverage
    target_net = used_margin * float(net_roe_target)
    return (entry + fees + target_net) / (qty * (1.0 - float(exit_fee_rate)))

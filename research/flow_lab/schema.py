from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal, Optional

Side = Literal["buy", "sell"]
Market = Literal["spot", "perp"]


@dataclass(frozen=True, slots=True)
class TradeEvent:
    exchange: str
    market: Market
    asset: str
    symbol: str
    ts_ms: int
    price: float
    qty_base: float
    notional_usd: float
    taker_side: Side
    trade_id: str
    recv_ts_ms: Optional[int] = None

    @property
    def signed_notional_usd(self) -> float:
        return self.notional_usd if self.taker_side == "buy" else -self.notional_usd

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class FlowBucket:
    exchange: str
    market: str
    asset: str
    start_ms: int
    end_ms: int
    trade_count: int
    buy_usd: float
    sell_usd: float
    delta_usd: float
    gross_usd: float
    flow_ratio: float
    open_price: float
    close_price: float
    high_price: float
    low_price: float
    return_bps: float

    def to_dict(self) -> dict:
        return asdict(self)

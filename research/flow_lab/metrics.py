from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from math import sqrt
from typing import Iterable, Sequence

from .schema import FlowBucket, TradeEvent


def _bucket_start(ts_ms: int, bucket_ms: int) -> int:
    return (int(ts_ms) // bucket_ms) * bucket_ms


def aggregate_events(events: Iterable[TradeEvent], bucket_ms: int = 1000) -> list[FlowBucket]:
    if bucket_ms <= 0:
        raise ValueError("bucket_ms must be positive")
    groups = defaultdict(list)
    for e in events:
        groups[(e.exchange, e.market, e.asset, _bucket_start(e.ts_ms, bucket_ms))].append(e)
    out = []
    for (exchange, market, asset, start), rows in sorted(groups.items(), key=lambda kv: kv[0][-1]):
        rows.sort(key=lambda x: x.ts_ms)
        buy = sum(x.notional_usd for x in rows if x.taker_side == "buy")
        sell = sum(x.notional_usd for x in rows if x.taker_side == "sell")
        gross, delta = buy + sell, buy - sell
        op, cp = rows[0].price, rows[-1].price
        prices = [x.price for x in rows]
        out.append(FlowBucket(
            exchange=exchange, market=market, asset=asset, start_ms=start, end_ms=start + bucket_ms,
            trade_count=len(rows), buy_usd=buy, sell_usd=sell, delta_usd=delta, gross_usd=gross,
            flow_ratio=(delta / gross) if gross else 0.0, open_price=op, close_price=cp,
            high_price=max(prices), low_price=min(prices),
            return_bps=((cp / op) - 1.0) * 10000.0 if op else 0.0,
        ))
    return out


def combine_venues(buckets: Sequence[FlowBucket]) -> list[dict]:
    """Combine aligned exchange buckets without hand-picked venue weights."""
    groups = defaultdict(list)
    for b in buckets:
        groups[(b.asset, b.market, b.start_ms, b.end_ms)].append(b)
    out = []
    for (asset, market, start_ms, end_ms), rows in sorted(groups.items()):
        buy, sell = sum(r.buy_usd for r in rows), sum(r.sell_usd for r in rows)
        gross, delta = buy + sell, buy - sell
        signed = [1 if r.delta_usd > 0 else -1 if r.delta_usd < 0 else 0 for r in rows]
        active = [x for x in signed if x]
        out.append({
            "asset": asset, "market": market, "start_ms": start_ms, "end_ms": end_ms,
            "venue_count": len(rows), "buy_venue_count": sum(x > 0 for x in signed),
            "sell_venue_count": sum(x < 0 for x in signed), "buy_usd": buy, "sell_usd": sell,
            "delta_usd": delta, "gross_usd": gross, "flow_ratio": (delta / gross) if gross else 0.0,
            "consensus": abs(sum(active)) / len(active) if active else 0.0,
        })
    return out


@dataclass(slots=True)
class NormalizedValue:
    value: float
    zscore: float | None
    percentile: float | None
    history_n: int


class RollingNormalizer:
    """Current observation is scored against past observations before it is appended."""
    def __init__(self, maxlen: int = 10000, min_history: int = 100):
        if maxlen < 2:
            raise ValueError("maxlen must be >= 2")
        self.values = deque(maxlen=maxlen)
        self.min_history = min_history

    def score_then_update(self, value: float) -> NormalizedValue:
        n = len(self.values)
        z = pct = None
        if n >= self.min_history:
            mean = sum(self.values) / n
            var = sum((x - mean) ** 2 for x in self.values) / n
            std = sqrt(var)
            z = (value - mean) / std if std > 0 else 0.0
            less = sum(x < value for x in self.values)
            equal = sum(x == value for x in self.values)
            pct = (less + 0.5 * equal) / n
        self.values.append(float(value))
        return NormalizedValue(float(value), z, pct, n)


def price_response_features(delta_usd: float, return_bps: float, gross_usd: float) -> dict:
    sign = 1.0 if delta_usd > 0 else -1.0 if delta_usd < 0 else 0.0
    return {
        "response_in_flow_direction_bps": return_bps * sign,
        "absolute_return_bps": abs(return_bps),
        "absolute_delta_usd": abs(delta_usd),
        "absolute_flow_ratio": abs(delta_usd) / gross_usd if gross_usd else 0.0,
        "delta_per_abs_return_usd_per_bp": abs(delta_usd) / max(abs(return_bps), 0.1),
    }

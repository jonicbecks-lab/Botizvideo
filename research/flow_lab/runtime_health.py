from __future__ import annotations

from .quality import StreamHealth, quality_gate
from .schema import TradeEvent


def trade_stream_id(exchange: str, market: str, asset: str) -> str:
    return f"trade:{exchange}:{market}:{asset}"


class RuntimeHealthRegistry:
    """Mutable point-in-time health state for live public feeds."""

    def __init__(self) -> None:
        self.streams: dict[str, StreamHealth] = {}

    def get(self, stream_id: str) -> StreamHealth:
        row = self.streams.get(stream_id)
        if row is None:
            row = StreamHealth(stream_id)
            self.streams[stream_id] = row
        return row

    def observe_trade(self, event: TradeEvent, *, duplicate: bool = False) -> str:
        stream_id = trade_stream_id(event.exchange, event.market, event.asset)
        h = self.get(stream_id)
        recv = int(event.recv_ts_ms if event.recv_ts_ms is not None else event.ts_ms)
        h.observe(int(event.ts_ms), recv)
        if duplicate:
            h.duplicate()
        return stream_id

    def reconnect_trade(self, exchange: str, market: str, assets: list[str]) -> None:
        for asset in assets:
            self.get(trade_stream_id(exchange, market, asset)).reconnect()

    def snapshot(self, *, required_streams: tuple[str, ...], now_ms: int,
                 max_stale_ms: int = 5000, max_event_lag_ms: int = 5000) -> dict:
        return quality_gate(
            self.streams, required_streams, now_ms,
            max_stale_ms=max_stale_ms, max_event_lag_ms=max_event_lag_ms,
        )

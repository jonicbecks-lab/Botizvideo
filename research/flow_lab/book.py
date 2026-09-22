from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable, Mapping, Sequence


def asset_from_symbol(symbol: str) -> str:
    s = symbol.upper()
    if s.startswith("BTC"):
        return "BTC"
    if s.startswith("ETH"):
        return "ETH"
    raise ValueError(f"unsupported asset symbol: {symbol}")


@dataclass(frozen=True, slots=True)
class BookSnapshot:
    exchange: str
    market: str
    asset: str
    symbol: str
    ts_ms: int
    bids: tuple[tuple[float, float], ...]
    asks: tuple[tuple[float, float], ...]
    recv_ts_ms: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class MutableBook:
    """Minimal price->base-qty book for snapshot+delta feeds."""

    def __init__(self) -> None:
        self.bids: dict[float, float] = {}
        self.asks: dict[float, float] = {}
        self.ready = False

    @staticmethod
    def _apply(side: dict[float, float], rows: Iterable[Sequence[str | float]]) -> None:
        for row in rows:
            px, qty = float(row[0]), float(row[1])
            if qty == 0:
                side.pop(px, None)
            else:
                side[px] = qty

    def snapshot(self, bids: Iterable[Sequence[str | float]], asks: Iterable[Sequence[str | float]]) -> None:
        self.bids.clear(); self.asks.clear()
        self._apply(self.bids, bids); self._apply(self.asks, asks)
        self.ready = True

    def delta(self, bids: Iterable[Sequence[str | float]], asks: Iterable[Sequence[str | float]]) -> None:
        if not self.ready:
            raise RuntimeError("delta received before snapshot")
        self._apply(self.bids, bids); self._apply(self.asks, asks)

    def export(self, *, exchange: str, market: str, symbol: str, ts_ms: int,
               qty_multiplier: float = 1.0, recv_ts_ms: int | None = None,
               max_levels: int = 200) -> BookSnapshot:
        bids = tuple((p, q * qty_multiplier) for p, q in sorted(self.bids.items(), reverse=True)[:max_levels])
        asks = tuple((p, q * qty_multiplier) for p, q in sorted(self.asks.items())[:max_levels])
        return BookSnapshot(exchange, market, asset_from_symbol(symbol), symbol, int(ts_ms), bids, asks, recv_ts_ms)


def parse_binance_partial_book(payload: Mapping, market: str, recv_ts_ms: int | None = None) -> BookSnapshot:
    data = payload.get("data", payload)
    symbol = str(data.get("s") or data.get("symbol"))
    bids = data.get("bids", data.get("b", []))
    asks = data.get("asks", data.get("a", []))
    ts = int(data.get("E", data.get("T", recv_ts_ms or 0)))
    return BookSnapshot(
        "binance", market, asset_from_symbol(symbol), symbol, ts,
        tuple((float(p), float(q)) for p, q, *_ in bids),
        tuple((float(p), float(q)) for p, q, *_ in asks), recv_ts_ms,
    )


def apply_bybit_orderbook(payload: Mapping, market: str, book: MutableBook,
                          recv_ts_ms: int | None = None) -> BookSnapshot | None:
    data = payload.get("data") or {}
    if not data or "s" not in data:
        return None
    bids, asks = data.get("b", []), data.get("a", [])
    if payload.get("type") == "snapshot" or int(data.get("u", 0)) == 1:
        book.snapshot(bids, asks)
    else:
        if not book.ready:
            return None
        book.delta(bids, asks)
    return book.export(exchange="bybit", market=market, symbol=str(data["s"]),
                       ts_ms=int(payload.get("cts", payload.get("ts", 0))), recv_ts_ms=recv_ts_ms)


def parse_okx_books_snapshot(payload: Mapping, market: str, qty_multiplier: float = 1.0,
                             recv_ts_ms: int | None = None) -> list[BookSnapshot]:
    out: list[BookSnapshot] = []
    symbol = str((payload.get("arg") or {}).get("instId", ""))
    if not symbol:
        return out
    for row in payload.get("data", []) or []:
        out.append(BookSnapshot(
            "okx", market, asset_from_symbol(symbol), symbol, int(row["ts"]),
            tuple((float(x[0]), float(x[1]) * qty_multiplier) for x in row.get("bids", [])),
            tuple((float(x[0]), float(x[1]) * qty_multiplier) for x in row.get("asks", [])),
            recv_ts_ms,
        ))
    return out


def parse_hyperliquid_l2book(payload: Mapping, recv_ts_ms: int | None = None) -> BookSnapshot | None:
    if payload.get("channel") != "l2Book":
        return None
    data = payload.get("data") or {}
    levels = data.get("levels") or [[], []]
    if len(levels) != 2:
        return None
    symbol = str(data["coin"])
    return BookSnapshot(
        "hyperliquid", "perp", asset_from_symbol(symbol), symbol, int(data["time"]),
        tuple((float(x["px"]), float(x["sz"])) for x in levels[0]),
        tuple((float(x["px"]), float(x["sz"])) for x in levels[1]), recv_ts_ms,
    )


def book_features(book: BookSnapshot, bands_bps: Sequence[int] = (5, 10, 25)) -> dict:
    if not book.bids or not book.asks:
        return {"book_valid": False}
    best_bid, best_ask = book.bids[0][0], book.asks[0][0]
    mid = (best_bid + best_ask) / 2.0
    out = {
        "book_valid": True,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "mid": mid,
        "spread_bps": ((best_ask - best_bid) / mid) * 10000.0 if mid else 0.0,
    }
    for bps in bands_bps:
        bid_floor = mid * (1.0 - bps / 10000.0)
        ask_ceil = mid * (1.0 + bps / 10000.0)
        bid_usd = sum(px * qty for px, qty in book.bids if px >= bid_floor)
        ask_usd = sum(px * qty for px, qty in book.asks if px <= ask_ceil)
        total = bid_usd + ask_usd
        out[f"bid_depth_usd_{bps}bps"] = bid_usd
        out[f"ask_depth_usd_{bps}bps"] = ask_usd
        out[f"imbalance_{bps}bps"] = (bid_usd - ask_usd) / total if total else 0.0
    return out


def depth_change_features(prev: BookSnapshot, cur: BookSnapshot, band_bps: int = 10) -> dict:
    if (prev.exchange, prev.market, prev.asset) != (cur.exchange, cur.market, cur.asset):
        raise ValueError("book identity mismatch")
    if not prev.bids or not prev.asks or not cur.bids or not cur.asks:
        return {"depth_change_valid": False}
    ref_mid = ((prev.bids[0][0] + prev.asks[0][0]) + (cur.bids[0][0] + cur.asks[0][0])) / 4.0
    lo, hi = ref_mid * (1 - band_bps / 10000.0), ref_mid * (1 + band_bps / 10000.0)

    def changes(old_rows, new_rows):
        old = {p: q for p, q in old_rows if lo <= p <= hi}
        new = {p: q for p, q in new_rows if lo <= p <= hi}
        added = removed = 0.0
        for p in set(old) | set(new):
            d = new.get(p, 0.0) - old.get(p, 0.0)
            usd = p * abs(d)
            if d > 0: added += usd
            elif d < 0: removed += usd
        return added, removed

    bid_add, bid_remove = changes(prev.bids, cur.bids)
    ask_add, ask_remove = changes(prev.asks, cur.asks)
    return {
        "depth_change_valid": True,
        "band_bps": band_bps,
        "bid_add_usd": bid_add,
        "bid_remove_usd": bid_remove,
        "ask_add_usd": ask_add,
        "ask_remove_usd": ask_remove,
        "bid_net_depth_change_usd": bid_add - bid_remove,
        "ask_net_depth_change_usd": ask_add - ask_remove,
    }


def replenishment_proxy(delta_usd: float, change: Mapping[str, float]) -> dict:
    """Diagnostic proxy only: book additions are not assumed to be absorption."""
    if not change.get("depth_change_valid"):
        return {"replenishment_valid": False}
    magnitude = max(abs(delta_usd), 1.0)
    return {
        "replenishment_valid": True,
        "bid_refill_vs_sell_flow": float(change["bid_add_usd"]) / magnitude if delta_usd < 0 else 0.0,
        "ask_refill_vs_buy_flow": float(change["ask_add_usd"]) / magnitude if delta_usd > 0 else 0.0,
    }

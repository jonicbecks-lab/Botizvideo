from __future__ import annotations

from typing import Mapping, Optional

from .schema import TradeEvent


def _asset_from_symbol(symbol: str) -> str:
    s = symbol.upper()
    if s.startswith("BTC"):
        return "BTC"
    if s.startswith("ETH"):
        return "ETH"
    raise ValueError(f"unsupported asset symbol: {symbol}")


def _event(*, exchange: str, market: str, symbol: str, ts_ms: int, price: float,
           qty_base: float, taker_side: str, trade_id: str,
           recv_ts_ms: Optional[int] = None) -> TradeEvent:
    if price <= 0 or qty_base < 0:
        raise ValueError("price must be > 0 and qty_base must be >= 0")
    side = taker_side.lower()
    if side not in {"buy", "sell"}:
        raise ValueError(f"invalid taker side: {taker_side}")
    return TradeEvent(
        exchange=exchange, market=market, asset=_asset_from_symbol(symbol), symbol=symbol,
        ts_ms=int(ts_ms), price=float(price), qty_base=float(qty_base),
        notional_usd=float(price) * float(qty_base), taker_side=side,
        trade_id=str(trade_id), recv_ts_ms=recv_ts_ms,
    )


def parse_binance_aggtrade(payload: Mapping, market: str, recv_ts_ms: Optional[int] = None) -> TradeEvent:
    """Binance m=True means buyer is maker, hence aggressing/taker side is SELL."""
    data = payload.get("data", payload)
    return _event(
        exchange="binance", market=market, symbol=str(data["s"]),
        ts_ms=int(data.get("T", data.get("E"))), price=float(data["p"]), qty_base=float(data["q"]),
        taker_side="sell" if bool(data["m"]) else "buy", trade_id=str(data["a"]),
        recv_ts_ms=recv_ts_ms,
    )


def parse_bybit_public_trade(payload: Mapping, market: str, recv_ts_ms: Optional[int] = None) -> list[TradeEvent]:
    out = []
    for row in payload.get("data", []) or []:
        out.append(_event(
            exchange="bybit", market=market, symbol=str(row["s"]),
            ts_ms=int(row.get("T", payload.get("ts", 0))), price=float(row["p"]), qty_base=float(row["v"]),
            taker_side=str(row["S"]).lower(), trade_id=str(row.get("i", f"{row.get('T')}:{row.get('p')}:{row.get('v')}")),
            recv_ts_ms=recv_ts_ms,
        ))
    return out


def parse_okx_trade(payload: Mapping, market: str,
                    contract_base_value: Optional[Mapping[str, float]] = None,
                    recv_ts_ms: Optional[int] = None) -> list[TradeEvent]:
    """Spot sz is base quantity; SWAP sz is contract count and needs contract metadata."""
    out = []
    for row in payload.get("data", []) or []:
        symbol = str(row["instId"])
        raw_qty = float(row["sz"])
        if market == "perp":
            if not contract_base_value or symbol not in contract_base_value:
                raise ValueError(f"missing OKX contract base value for {symbol}")
            qty_base = raw_qty * float(contract_base_value[symbol])
        else:
            qty_base = raw_qty
        out.append(_event(
            exchange="okx", market=market, symbol=symbol, ts_ms=int(row["ts"]),
            price=float(row["px"]), qty_base=qty_base, taker_side=str(row["side"]).lower(),
            trade_id=str(row["tradeId"]), recv_ts_ms=recv_ts_ms,
        ))
    return out


def parse_hyperliquid_trade(payload: Mapping, recv_ts_ms: Optional[int] = None) -> list[TradeEvent]:
    if payload.get("channel") != "trades":
        return []
    out = []
    for row in payload.get("data", []) or []:
        side_raw = str(row["side"]).upper()
        if side_raw not in {"B", "A"}:
            raise ValueError(f"unexpected Hyperliquid trade side: {side_raw}")
        out.append(_event(
            exchange="hyperliquid", market="perp", symbol=str(row["coin"]), ts_ms=int(row["time"]),
            price=float(row["px"]), qty_base=float(row["sz"]),
            taker_side="buy" if side_raw == "B" else "sell",
            trade_id=str(row.get("tid", row.get("hash", ""))), recv_ts_ms=recv_ts_ms,
        ))
    return out

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Mapping

import requests

from .book import asset_from_symbol


@dataclass(frozen=True, slots=True)
class DerivativesContext:
    exchange: str
    asset: str
    symbol: str
    ts_ms: int
    mark_price: float | None = None
    open_interest_base: float | None = None
    open_interest_usd: float | None = None
    funding_rate: float | None = None
    recv_ts_ms: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class LiquidationEvent:
    exchange: str
    asset: str
    symbol: str
    ts_ms: int
    liquidated_side: str
    price: float
    qty_base: float
    notional_usd: float
    recv_ts_ms: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def parse_bybit_ticker(payload: Mapping, recv_ts_ms: int | None = None) -> DerivativesContext | None:
    if not str(payload.get("topic", "")).startswith("tickers."):
        return None
    data = payload.get("data") or {}
    if isinstance(data, list):
        data = data[0] if data else {}
    symbol = str(data.get("symbol") or str(payload["topic"]).split(".")[-1])
    mark = float(data["markPrice"]) if data.get("markPrice") not in {None, ""} else None
    oi_base = float(data["openInterest"]) if data.get("openInterest") not in {None, ""} else None
    oi_usd = float(data["openInterestValue"]) if data.get("openInterestValue") not in {None, ""} else None
    funding = float(data["fundingRate"]) if data.get("fundingRate") not in {None, ""} else None
    return DerivativesContext("bybit", asset_from_symbol(symbol), symbol, int(payload.get("ts", recv_ts_ms or 0)),
                              mark, oi_base, oi_usd, funding, recv_ts_ms)


def parse_bybit_liquidations(payload: Mapping, recv_ts_ms: int | None = None) -> list[LiquidationEvent]:
    if not str(payload.get("topic", "")).startswith("allLiquidation."):
        return []
    out = []
    for row in payload.get("data", []) or []:
        symbol = str(row["s"])
        price, qty = float(row["p"]), float(row["v"])
        # Bybit docs: S=Buy means a LONG position was liquidated; S=Sell means SHORT.
        side = "long" if str(row["S"]).lower() == "buy" else "short"
        out.append(LiquidationEvent("bybit", asset_from_symbol(symbol), symbol, int(row["T"]),
                                    side, price, qty, price * qty, recv_ts_ms))
    return out


def parse_hyperliquid_asset_ctx(payload: Mapping, recv_ts_ms: int | None = None) -> DerivativesContext | None:
    if payload.get("channel") != "activeAssetCtx":
        return None
    data = payload.get("data") or {}
    ctx = data.get("ctx") or {}
    symbol = str(data["coin"])
    mark = float(ctx["markPx"]) if ctx.get("markPx") not in {None, ""} else None
    oi_base = float(ctx["openInterest"]) if ctx.get("openInterest") not in {None, ""} else None
    oi_usd = oi_base * mark if oi_base is not None and mark is not None else None
    funding = float(ctx["funding"]) if ctx.get("funding") not in {None, ""} else None
    return DerivativesContext("hyperliquid", asset_from_symbol(symbol), symbol,
                              int(recv_ts_ms or 0), mark, oi_base, oi_usd, funding, recv_ts_ms)


def parse_okx_funding(payload: Mapping, recv_ts_ms: int | None = None) -> DerivativesContext | None:
    arg = payload.get("arg") or {}
    if arg.get("channel") != "funding-rate":
        return None
    rows = payload.get("data", []) or []
    if not rows:
        return None
    row = rows[-1]
    symbol = str(row["instId"])
    funding = float(row["fundingRate"]) if row.get("fundingRate") not in {None, ""} else None
    ts = int(row.get("ts") or row.get("fundingTime") or recv_ts_ms or 0)
    return DerivativesContext("okx", asset_from_symbol(symbol), symbol, ts,
                              funding_rate=funding, recv_ts_ms=recv_ts_ms)


def fetch_binance_open_interest(symbol: str, timeout: float = 10.0) -> DerivativesContext:
    r = requests.get("https://fapi.binance.com/fapi/v1/openInterest", params={"symbol": symbol}, timeout=timeout)
    r.raise_for_status()
    row = r.json()
    oi_base = float(row["openInterest"])
    return DerivativesContext("binance", asset_from_symbol(symbol), symbol, int(row.get("time", 0)),
                              open_interest_base=oi_base)


def fetch_okx_open_interest(symbol: str, timeout: float = 10.0) -> DerivativesContext:
    r = requests.get("https://openapi.okx.com/api/v5/public/open-interest",
                     params={"instType": "SWAP", "instId": symbol}, timeout=timeout)
    r.raise_for_status()
    body = r.json()
    if body.get("code") not in {None, "0", 0}:
        raise RuntimeError(f"OKX open-interest error: {body}")
    rows = body.get("data", [])
    if not rows:
        raise RuntimeError(f"OKX open-interest empty for {symbol}")
    row = rows[-1]
    oi_base = float(row["oiCcy"]) if row.get("oiCcy") not in {None, ""} else None
    oi_usd = float(row["oiUsd"]) if row.get("oiUsd") not in {None, ""} else None
    return DerivativesContext("okx", asset_from_symbol(symbol), symbol, int(row.get("ts", 0)),
                              open_interest_base=oi_base, open_interest_usd=oi_usd)

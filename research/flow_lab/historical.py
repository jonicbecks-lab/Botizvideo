from __future__ import annotations

import csv
import io
import zipfile
from datetime import date, timedelta
from itertools import chain
from pathlib import Path
from typing import Iterator

import requests

from .schema import TradeEvent

BINANCE_VISION = "https://data.binance.vision/data"


def _ms_timestamp(value: str | int) -> int:
    x = int(value)
    # Public archives may use microseconds; normalize to milliseconds.
    return x // 1000 if x > 100_000_000_000_000 else x


def binance_vision_aggtrades_url(symbol: str, day: date, market: str = "perp") -> str:
    symbol = symbol.upper()
    ds = day.isoformat()
    if market == "perp":
        return f"{BINANCE_VISION}/futures/um/daily/aggTrades/{symbol}/{symbol}-aggTrades-{ds}.zip"
    if market == "spot":
        return f"{BINANCE_VISION}/spot/daily/aggTrades/{symbol}/{symbol}-aggTrades-{ds}.zip"
    raise ValueError("market must be spot or perp")


def download_file(url: str, destination: str | Path, timeout: float = 60.0) -> Path:
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        with path.open("wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)
    return path


def _bool(value: str) -> bool:
    return str(value).strip().lower() in {"true", "1", "t", "yes"}


def _row_to_event(row: list[str], symbol: str, market: str) -> TradeEvent:
    if len(row) < 7:
        raise ValueError(f"unexpected aggTrades row with {len(row)} columns")
    agg_id, price, qty, _first_id, _last_id, ts, buyer_maker = row[:7]
    p, q = float(price), float(qty)
    upper = symbol.upper()
    if upper.startswith("BTC"):
        asset = "BTC"
    elif upper.startswith("ETH"):
        asset = "ETH"
    else:
        raise ValueError(f"unsupported archive symbol: {symbol}")
    return TradeEvent(
        exchange="binance", market=market, asset=asset, symbol=upper,
        ts_ms=_ms_timestamp(ts), price=p, qty_base=q, notional_usd=p * q,
        taker_side="sell" if _bool(buyer_maker) else "buy", trade_id=str(agg_id),
    )


def iter_binance_aggtrades_zip(path: str | Path, symbol: str, market: str = "perp") -> Iterator[TradeEvent]:
    with zipfile.ZipFile(path) as zf:
        names = [n for n in zf.namelist() if not n.endswith("/")]
        if len(names) != 1:
            raise ValueError(f"expected one CSV in archive, found {len(names)}")
        with zf.open(names[0]) as raw:
            text = io.TextIOWrapper(raw, encoding="utf-8", newline="")
            reader = csv.reader(text)
            first = next(reader, None)
            if first is None:
                return
            # Some archive generations contain a header; others do not.
            rows = reader if first[0].strip().lower() in {"agg_trade_id", "aggtradeid", "id"} else chain([first], reader)
            for row in rows:
                if row:
                    yield _row_to_event(row, symbol, market)


def download_binance_days(symbol: str, start: date, end: date, output_dir: str | Path,
                          market: str = "perp") -> list[Path]:
    if end < start:
        raise ValueError("end must be >= start")
    out = []
    d = start
    while d <= end:
        url = binance_vision_aggtrades_url(symbol, d, market)
        name = url.rsplit("/", 1)[-1]
        out.append(download_file(url, Path(output_dir) / name))
        d += timedelta(days=1)
    return out

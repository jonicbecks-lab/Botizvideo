from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

import websockets

from .book import (
    BookSnapshot, MutableBook, apply_bybit_orderbook, book_features,
    depth_change_features, parse_binance_partial_book, parse_hyperliquid_l2book,
    parse_okx_books_snapshot,
)
from .collector import (
    BINANCE_FUTURES_WS, BINANCE_SPOT_WS, BYBIT_LINEAR_WS, BYBIT_SPOT_WS,
    HYPERLIQUID_WS, OKX_PUBLIC_WS, SYMBOLS, _forever,
)
from .context import (
    fetch_binance_open_interest, fetch_okx_open_interest, parse_bybit_liquidations,
    parse_bybit_ticker, parse_hyperliquid_asset_ctx, parse_okx_funding,
)
from .okx_metadata import fetch_linear_swap_base_values
from .storage import AsyncJsonlDirectory


class JsonlSink:
    """Buffered multi-file sink for book/context/health streams."""

    def __init__(self, root: str | Path):
        self.directory = AsyncJsonlDirectory(root)

    async def write(self, name: str, row: dict) -> None:
        await self.directory.write(name, row)

    async def close(self) -> None:
        await self.directory.close()

    def stats(self) -> dict:
        return self.directory.stats()


class LatestBooks:
    def __init__(self):
        self.latest: dict[tuple[str, str, str], BookSnapshot] = {}
        self.previous_emitted: dict[tuple[str, str, str], BookSnapshot] = {}

    def put(self, book: BookSnapshot) -> None:
        self.latest[(book.exchange, book.market, book.asset)] = book


async def _emit_book_features(state: LatestBooks, sink: JsonlSink, emit_ms: int) -> None:
    while True:
        await asyncio.sleep(emit_ms / 1000.0)
        for key, cur in list(state.latest.items()):
            row = {
                "exchange": cur.exchange, "market": cur.market, "asset": cur.asset,
                "symbol": cur.symbol, "ts_ms": cur.ts_ms, "recv_ts_ms": cur.recv_ts_ms,
                "emit_ts_ms": int(time.time() * 1000),
            }
            row.update(book_features(cur))
            prev = state.previous_emitted.get(key)
            if prev is not None:
                row.update(depth_change_features(prev, cur, 10))
            else:
                row["depth_change_valid"] = False
            state.previous_emitted[key] = cur
            await sink.write("book_features", row)


async def _binance_books(url: str, market: str, assets: list[str], state: LatestBooks) -> None:
    params = [f"{SYMBOLS[a]['binance']}@depth20@100ms" for a in assets]
    async with websockets.connect(url, ping_interval=20, ping_timeout=20, max_queue=8192) as ws:
        await ws.send(json.dumps({"method": "SUBSCRIBE", "params": params, "id": 11}))
        async for raw in ws:
            msg = json.loads(raw)
            data = msg.get("data", msg)
            if not isinstance(data, dict) or not ("bids" in data or "b" in data):
                continue
            if not data.get("s"):
                stream = str(msg.get("stream", ""))
                if "@" in stream:
                    data = dict(data)
                    data["s"] = stream.split("@", 1)[0].upper()
                    msg = {"data": data}
            try:
                state.put(parse_binance_partial_book(msg, market, int(time.time() * 1000)))
            except (KeyError, ValueError):
                continue


async def _bybit_micro(url: str, market: str, assets: list[str], state: LatestBooks, sink: JsonlSink) -> None:
    topics = [f"orderbook.50.{SYMBOLS[a]['bybit']}" for a in assets]
    if market == "perp":
        topics += [f"tickers.{SYMBOLS[a]['bybit']}" for a in assets]
        topics += [f"allLiquidation.{SYMBOLS[a]['bybit']}" for a in assets]
    books = {SYMBOLS[a]["bybit"]: MutableBook() for a in assets}
    async with websockets.connect(url, ping_interval=20, ping_timeout=20, max_queue=16384) as ws:
        await ws.send(json.dumps({"op": "subscribe", "args": topics}))
        async for raw in ws:
            msg = json.loads(raw)
            topic = str(msg.get("topic", ""))
            recv = int(time.time() * 1000)
            if topic.startswith("orderbook."):
                symbol = topic.rsplit(".", 1)[-1]
                book = apply_bybit_orderbook(msg, market, books[symbol], recv)
                if book:
                    state.put(book)
            elif topic.startswith("tickers."):
                ctx = parse_bybit_ticker(msg, recv)
                if ctx:
                    await sink.write("derivatives_context", ctx.to_dict())
            elif topic.startswith("allLiquidation."):
                for event in parse_bybit_liquidations(msg, recv):
                    await sink.write("liquidations", event.to_dict())


async def _okx_micro(market: str, assets: list[str], state: LatestBooks, sink: JsonlSink,
                     contract_values: dict[str, float]) -> None:
    key = "okx_spot" if market == "spot" else "okx_perp"
    args = [{"channel": "books5", "instId": SYMBOLS[a][key]} for a in assets]
    if market == "perp":
        args += [{"channel": "funding-rate", "instId": SYMBOLS[a][key]} for a in assets]
    async with websockets.connect(OKX_PUBLIC_WS, ping_interval=20, ping_timeout=20, max_queue=8192) as ws:
        await ws.send(json.dumps({"op": "subscribe", "args": args}))
        async for raw in ws:
            if raw == "pong":
                continue
            msg = json.loads(raw)
            arg = msg.get("arg") or {}
            recv = int(time.time() * 1000)
            if arg.get("channel") == "books5":
                symbol = str(arg.get("instId", ""))
                multiplier = contract_values.get(symbol, 1.0) if market == "perp" else 1.0
                for book in parse_okx_books_snapshot(msg, market, multiplier, recv):
                    state.put(book)
            elif arg.get("channel") == "funding-rate":
                ctx = parse_okx_funding(msg, recv)
                if ctx:
                    await sink.write("derivatives_context", ctx.to_dict())


async def _hyperliquid_micro(assets: list[str], state: LatestBooks, sink: JsonlSink) -> None:
    async with websockets.connect(HYPERLIQUID_WS, ping_interval=20, ping_timeout=20, max_queue=8192) as ws:
        for asset in assets:
            await ws.send(json.dumps({"method": "subscribe", "subscription": {"type": "l2Book", "coin": asset}}))
            await ws.send(json.dumps({"method": "subscribe", "subscription": {"type": "activeAssetCtx", "coin": asset}}))
        async for raw in ws:
            msg = json.loads(raw)
            recv = int(time.time() * 1000)
            book = parse_hyperliquid_l2book(msg, recv)
            if book:
                state.put(book)
            ctx = parse_hyperliquid_asset_ctx(msg, recv)
            if ctx:
                await sink.write("derivatives_context", ctx.to_dict())


async def _poll_open_interest(assets: list[str], sink: JsonlSink, every_sec: float = 5.0) -> None:
    while True:
        started = time.monotonic()
        for asset in assets:
            for fn, symbol in (
                (fetch_binance_open_interest, f"{asset}USDT"),
                (fetch_okx_open_interest, f"{asset}-USDT-SWAP"),
            ):
                try:
                    ctx = await asyncio.to_thread(fn, symbol)
                    row = ctx.to_dict()
                    row["recv_ts_ms"] = int(time.time() * 1000)
                    await sink.write("derivatives_context", row)
                except Exception as exc:
                    await sink.write("health", {"ts_ms": int(time.time()*1000), "source": symbol,
                                                "kind": "open_interest", "error": str(exc)})
        await asyncio.sleep(max(0.1, every_sec - (time.monotonic() - started)))


async def run(root: str | Path, assets: list[str], emit_ms: int = 1000) -> None:
    assets = [a.upper() for a in assets]
    bad = set(assets) - set(SYMBOLS)
    if bad:
        raise ValueError(f"unsupported assets: {sorted(bad)}")
    sink, state = JsonlSink(root), LatestBooks()
    okx_perps = [SYMBOLS[a]["okx_perp"] for a in assets]
    contract_values = await asyncio.to_thread(fetch_linear_swap_base_values, okx_perps)
    try:
        await asyncio.gather(
            _forever("micro-binance-perp", lambda: _binance_books(BINANCE_FUTURES_WS, "perp", assets, state)),
            _forever("micro-binance-spot", lambda: _binance_books(BINANCE_SPOT_WS, "spot", assets, state)),
            _forever("micro-bybit-perp", lambda: _bybit_micro(BYBIT_LINEAR_WS, "perp", assets, state, sink)),
            _forever("micro-bybit-spot", lambda: _bybit_micro(BYBIT_SPOT_WS, "spot", assets, state, sink)),
            _forever("micro-okx-perp", lambda: _okx_micro("perp", assets, state, sink, contract_values)),
            _forever("micro-okx-spot", lambda: _okx_micro("spot", assets, state, sink, contract_values)),
            _forever("micro-hyperliquid", lambda: _hyperliquid_micro(assets, state, sink)),
            _emit_book_features(state, sink, emit_ms),
            _poll_open_interest(assets, sink),
        )
    finally:
        await sink.close()
        print("[flow-lab] micro collector final stats:", json.dumps(sink.stats(), sort_keys=True))


def main() -> None:
    p = argparse.ArgumentParser(description="Public BTC/ETH order-book + derivatives context collector")
    p.add_argument("--assets", nargs="+", default=["BTC", "ETH"])
    p.add_argument("--output-dir", default="data/flow/micro")
    p.add_argument("--book-emit-ms", type=int, default=1000)
    args = p.parse_args()
    if args.book_emit_ms < 100:
        raise SystemExit("--book-emit-ms must be >= 100")
    asyncio.run(run(args.output_dir, args.assets, args.book_emit_ms))


if __name__ == "__main__":
    main()

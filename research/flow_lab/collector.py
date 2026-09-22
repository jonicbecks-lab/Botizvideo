from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Awaitable, Callable

import websockets

from .okx_metadata import fetch_linear_swap_base_values
from .parsers import parse_binance_aggtrade, parse_bybit_public_trade, parse_hyperliquid_trade, parse_okx_trade
from .schema import TradeEvent

BINANCE_FUTURES_WS = "wss://fstream.binance.com/market/stream"
BINANCE_SPOT_WS = "wss://data-stream.binance.vision/stream"
BYBIT_LINEAR_WS = "wss://stream.bybit.com/v5/public/linear"
BYBIT_SPOT_WS = "wss://stream.bybit.com/v5/public/spot"
OKX_PUBLIC_WS = "wss://ws.okx.com:8443/ws/v5/public"
HYPERLIQUID_WS = "wss://api.hyperliquid.xyz/ws"

SYMBOLS = {
    "BTC": {"binance": "btcusdt", "bybit": "BTCUSDT", "okx_spot": "BTC-USDT", "okx_perp": "BTC-USDT-SWAP", "hyperliquid": "BTC"},
    "ETH": {"binance": "ethusdt", "bybit": "ETHUSDT", "okx_spot": "ETH-USDT", "okx_perp": "ETH-USDT-SWAP", "hyperliquid": "ETH"},
}


class JsonlSink:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    async def write(self, event: TradeEvent) -> None:
        line = json.dumps(event.to_dict(), separators=(",", ":"), sort_keys=True)
        async with self._lock:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")


async def _forever(name: str, worker: Callable[[], Awaitable[None]]) -> None:
    delay = 1.0
    while True:
        try:
            await worker()
            delay = 1.0
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[{name}] disconnected/error: {exc}; retry in {delay:.1f}s")
            await asyncio.sleep(delay)
            delay = min(delay * 2.0, 30.0)


async def _binance(url: str, market: str, assets: list[str], sink: JsonlSink) -> None:
    params = [f"{SYMBOLS[a]['binance']}@aggTrade" for a in assets]
    async with websockets.connect(url, ping_interval=20, ping_timeout=20, max_queue=8192) as ws:
        await ws.send(json.dumps({"method": "SUBSCRIBE", "params": params, "id": 1}))
        async for raw in ws:
            msg = json.loads(raw)
            data = msg.get("data", msg)
            if data.get("e") != "aggTrade":
                continue
            await sink.write(parse_binance_aggtrade(msg, market, int(time.time() * 1000)))


async def _bybit(url: str, market: str, assets: list[str], sink: JsonlSink) -> None:
    args = [f"publicTrade.{SYMBOLS[a]['bybit']}" for a in assets]
    async with websockets.connect(url, ping_interval=20, ping_timeout=20, max_queue=8192) as ws:
        await ws.send(json.dumps({"op": "subscribe", "args": args}))
        async for raw in ws:
            msg = json.loads(raw)
            if not str(msg.get("topic", "")).startswith("publicTrade."):
                continue
            for event in parse_bybit_public_trade(msg, market, int(time.time() * 1000)):
                await sink.write(event)


async def _okx(market: str, assets: list[str], sink: JsonlSink, contract_values: dict[str, float]) -> None:
    key = "okx_spot" if market == "spot" else "okx_perp"
    args = [{"channel": "trades", "instId": SYMBOLS[a][key]} for a in assets]
    async with websockets.connect(OKX_PUBLIC_WS, ping_interval=20, ping_timeout=20, max_queue=8192) as ws:
        await ws.send(json.dumps({"op": "subscribe", "args": args}))
        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("arg", {}).get("channel") != "trades":
                continue
            for event in parse_okx_trade(msg, market, contract_values, int(time.time() * 1000)):
                await sink.write(event)


async def _hyperliquid(assets: list[str], sink: JsonlSink) -> None:
    async with websockets.connect(HYPERLIQUID_WS, ping_interval=20, ping_timeout=20, max_queue=8192) as ws:
        for asset in assets:
            await ws.send(json.dumps({"method": "subscribe", "subscription": {"type": "trades", "coin": SYMBOLS[asset]["hyperliquid"]}}))
        async for raw in ws:
            msg = json.loads(raw)
            for event in parse_hyperliquid_trade(msg, int(time.time() * 1000)):
                await sink.write(event)


async def collect(path: str | Path, assets: list[str]) -> None:
    assets = [a.upper() for a in assets]
    bad = set(assets) - set(SYMBOLS)
    if bad:
        raise ValueError(f"unsupported assets: {sorted(bad)}")
    sink = JsonlSink(path)
    okx_perps = [SYMBOLS[a]["okx_perp"] for a in assets]
    contract_values = await asyncio.to_thread(fetch_linear_swap_base_values, okx_perps)
    await asyncio.gather(
        _forever("binance-perp", lambda: _binance(BINANCE_FUTURES_WS, "perp", assets, sink)),
        _forever("binance-spot", lambda: _binance(BINANCE_SPOT_WS, "spot", assets, sink)),
        _forever("bybit-perp", lambda: _bybit(BYBIT_LINEAR_WS, "perp", assets, sink)),
        _forever("bybit-spot", lambda: _bybit(BYBIT_SPOT_WS, "spot", assets, sink)),
        _forever("okx-perp", lambda: _okx("perp", assets, sink, contract_values)),
        _forever("okx-spot", lambda: _okx("spot", assets, sink, contract_values)),
        _forever("hyperliquid-perp", lambda: _hyperliquid(assets, sink)),
    )

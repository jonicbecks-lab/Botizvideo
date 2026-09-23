from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Awaitable, Callable

import websockets

from .dedupe import TradeDeduper
from .okx_metadata import fetch_linear_swap_base_values
from .parsers import parse_binance_aggtrade, parse_bybit_public_trade, parse_hyperliquid_trade, parse_okx_trade
from .runtime_health import RuntimeHealthRegistry, trade_stream_id
from .schema import TradeEvent
from .storage import AsyncJsonlWriter

# Current public request-subscription endpoints (2026 Binance API catalog).
BINANCE_FUTURES_WS = "wss://fstream.binance.com/public/stream"
BINANCE_SPOT_WS = "wss://stream.binance.com:9443/stream"
BYBIT_LINEAR_WS = "wss://stream.bybit.com/v5/public/linear"
BYBIT_SPOT_WS = "wss://stream.bybit.com/v5/public/spot"
OKX_PUBLIC_WS = "wss://ws.okx.com:8443/ws/v5/public"
HYPERLIQUID_WS = "wss://api.hyperliquid.xyz/ws"

SYMBOLS = {
    "BTC": {"binance": "btcusdt", "bybit": "BTCUSDT", "okx_spot": "BTC-USDT", "okx_perp": "BTC-USDT-SWAP", "hyperliquid": "BTC"},
    "ETH": {"binance": "ethusdt", "bybit": "ETHUSDT", "okx_spot": "ETH-USDT", "okx_perp": "ETH-USDT-SWAP", "hyperliquid": "ETH"},
}


class JsonlSink:
    """Buffered trade sink with deduplication and point-in-time health telemetry."""

    def __init__(self, path: str | Path, *, dedupe_max_keys: int = 200_000):
        path = Path(path)
        health_path = path.with_name(f"{path.stem}.health.jsonl")
        self.writer = AsyncJsonlWriter(path)
        self.health_writer = AsyncJsonlWriter(health_path, batch_size=50, flush_interval_sec=1.0)
        self.deduper = TradeDeduper(dedupe_max_keys)
        self.health = RuntimeHealthRegistry()

    async def start(self) -> None:
        await self.writer.start()
        await self.health_writer.start()

    async def write(self, event: TradeEvent) -> bool:
        accepted = self.deduper.accept(event)
        self.health.observe_trade(event, duplicate=not accepted)
        if not accepted:
            return False
        await self.writer.write(event.to_dict())
        return True

    def reconnect(self, exchange: str, market: str, assets: list[str]) -> None:
        self.health.reconnect_trade(exchange, market, assets)

    async def write_health(self, row: dict) -> None:
        await self.health_writer.write(row)

    async def close(self) -> None:
        await asyncio.gather(self.writer.close(), self.health_writer.close())

    def stats(self) -> dict:
        return {
            "storage": self.writer.stats(),
            "health_storage": self.health_writer.stats(),
            "dedupe": self.deduper.stats(),
        }


async def _forever(name: str, worker: Callable[[], Awaitable[None]],
                   on_reconnect: Callable[[str], None] | None = None) -> None:
    delay = 1.0
    attempts = 0
    while True:
        try:
            if attempts and on_reconnect is not None:
                on_reconnect(name)
            await worker()
            delay = 1.0
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            attempts += 1
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


async def _emit_trade_health(sink: JsonlSink, required_streams: tuple[str, ...],
                             every_sec: float = 5.0) -> None:
    while True:
        await asyncio.sleep(every_sec)
        now_ms = int(time.time() * 1000)
        row = sink.health.snapshot(
            required_streams=required_streams,
            now_ms=now_ms,
            max_stale_ms=5000,
            max_event_lag_ms=5000,
        )
        row["kind"] = "trade_quality_gate"
        row["dedupe"] = sink.deduper.stats()
        row["storage"] = sink.writer.stats()
        await sink.write_health(row)


def _required_trade_streams(assets: list[str]) -> tuple[str, ...]:
    sources = (
        ("binance", "perp"), ("binance", "spot"),
        ("bybit", "perp"), ("bybit", "spot"),
        ("okx", "perp"), ("okx", "spot"),
        ("hyperliquid", "perp"),
    )
    return tuple(trade_stream_id(exchange, market, asset)
                 for exchange, market in sources for asset in assets)


async def collect(path: str | Path, assets: list[str]) -> None:
    assets = [a.upper() for a in assets]
    bad = set(assets) - set(SYMBOLS)
    if bad:
        raise ValueError(f"unsupported assets: {sorted(bad)}")
    sink = JsonlSink(path)
    await sink.start()
    required_streams = _required_trade_streams(assets)
    okx_perps = [SYMBOLS[a]["okx_perp"] for a in assets]
    contract_values = await asyncio.to_thread(fetch_linear_swap_base_values, okx_perps)
    try:
        await asyncio.gather(
            _forever("binance-perp", lambda: _binance(BINANCE_FUTURES_WS, "perp", assets, sink),
                     lambda _: sink.reconnect("binance", "perp", assets)),
            _forever("binance-spot", lambda: _binance(BINANCE_SPOT_WS, "spot", assets, sink),
                     lambda _: sink.reconnect("binance", "spot", assets)),
            _forever("bybit-perp", lambda: _bybit(BYBIT_LINEAR_WS, "perp", assets, sink),
                     lambda _: sink.reconnect("bybit", "perp", assets)),
            _forever("bybit-spot", lambda: _bybit(BYBIT_SPOT_WS, "spot", assets, sink),
                     lambda _: sink.reconnect("bybit", "spot", assets)),
            _forever("okx-perp", lambda: _okx("perp", assets, sink, contract_values),
                     lambda _: sink.reconnect("okx", "perp", assets)),
            _forever("okx-spot", lambda: _okx("spot", assets, sink, contract_values),
                     lambda _: sink.reconnect("okx", "spot", assets)),
            _forever("hyperliquid-perp", lambda: _hyperliquid(assets, sink),
                     lambda _: sink.reconnect("hyperliquid", "perp", assets)),
            _emit_trade_health(sink, required_streams),
        )
    finally:
        await sink.close()
        print("[flow-lab] collector final stats:", json.dumps(sink.stats(), sort_keys=True))

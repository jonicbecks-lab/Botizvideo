from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import dataclass, field

from .collector import (
    BINANCE_FUTURES_WS, BINANCE_SPOT_WS, BYBIT_LINEAR_WS, BYBIT_SPOT_WS,
    SYMBOLS, _binance, _bybit, _hyperliquid, _okx,
)
from .micro_collector import (
    LatestBooks, _binance_books, _bybit_micro, _hyperliquid_micro, _okx_micro,
)
from .okx_metadata import fetch_linear_swap_base_values
from .schema import TradeEvent


class ProbeComplete(Exception):
    pass


@dataclass
class TradeProbeSink:
    expected_assets: set[str]
    seen_assets: set[str] = field(default_factory=set)
    count: int = 0
    first_recv_ms: int | None = None
    last_recv_ms: int | None = None

    async def write(self, event: TradeEvent) -> bool:
        now = int(time.time() * 1000)
        self.count += 1
        self.seen_assets.add(event.asset)
        self.first_recv_ms = now if self.first_recv_ms is None else self.first_recv_ms
        self.last_recv_ms = now
        if self.expected_assets <= self.seen_assets:
            raise ProbeComplete
        return True


@dataclass
class BookProbeState(LatestBooks):
    expected_assets: set[str] = field(default_factory=set)
    seen_assets: set[str] = field(default_factory=set)
    count: int = 0

    def __init__(self, expected_assets: set[str]):
        super().__init__()
        self.expected_assets = set(expected_assets)
        self.seen_assets = set()
        self.count = 0

    def put(self, book) -> None:
        super().put(book)
        self.count += 1
        self.seen_assets.add(book.asset)
        if self.expected_assets <= self.seen_assets:
            raise ProbeComplete


class NullMicroSink:
    async def write(self, name: str, row: dict) -> None:
        return None


async def _probe(name: str, worker, observed, timeout_sec: float) -> dict:
    started = time.monotonic()
    error = None
    try:
        await asyncio.wait_for(worker(), timeout=timeout_sec)
    except ProbeComplete:
        pass
    except asyncio.TimeoutError:
        error = f"timeout after {timeout_sec}s"
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    duration = time.monotonic() - started
    seen = sorted(observed.seen_assets)
    expected = sorted(observed.expected_assets)
    passed = error is None and set(expected) <= set(seen)
    return {
        "probe": name,
        "pass": passed,
        "duration_sec": round(duration, 3),
        "expected_assets": expected,
        "seen_assets": seen,
        "message_count": observed.count,
        "error": error,
    }


async def run(timeout_sec: float = 15.0) -> dict:
    assets = ["BTC", "ETH"]
    expected = set(assets)
    okx_perps = [SYMBOLS[a]["okx_perp"] for a in assets]
    contract_values = await asyncio.to_thread(fetch_linear_swap_base_values, okx_perps)

    probes = []

    def trade_probe(name, factory):
        sink = TradeProbeSink(set(expected))
        probes.append(_probe(name, lambda: factory(sink), sink, timeout_sec))

    trade_probe("trade:binance-perp", lambda s: _binance(BINANCE_FUTURES_WS, "perp", assets, s))
    trade_probe("trade:binance-spot", lambda s: _binance(BINANCE_SPOT_WS, "spot", assets, s))
    trade_probe("trade:bybit-perp", lambda s: _bybit(BYBIT_LINEAR_WS, "perp", assets, s))
    trade_probe("trade:bybit-spot", lambda s: _bybit(BYBIT_SPOT_WS, "spot", assets, s))
    trade_probe("trade:okx-perp", lambda s: _okx("perp", assets, s, contract_values))
    trade_probe("trade:okx-spot", lambda s: _okx("spot", assets, s, contract_values))
    trade_probe("trade:hyperliquid-perp", lambda s: _hyperliquid(assets, s))

    null_sink = NullMicroSink()

    def book_probe(name, factory):
        state = BookProbeState(set(expected))
        probes.append(_probe(name, lambda: factory(state), state, timeout_sec))

    book_probe("book:binance-perp", lambda st: _binance_books(BINANCE_FUTURES_WS, "perp", assets, st))
    book_probe("book:binance-spot", lambda st: _binance_books(BINANCE_SPOT_WS, "spot", assets, st))
    book_probe("book:bybit-perp", lambda st: _bybit_micro(BYBIT_LINEAR_WS, "perp", assets, st, null_sink))
    book_probe("book:bybit-spot", lambda st: _bybit_micro(BYBIT_SPOT_WS, "spot", assets, st, null_sink))
    book_probe("book:okx-perp", lambda st: _okx_micro("perp", assets, st, null_sink, contract_values))
    book_probe("book:okx-spot", lambda st: _okx_micro("spot", assets, st, null_sink, contract_values))
    book_probe("book:hyperliquid-perp", lambda st: _hyperliquid_micro(assets, st, null_sink))

    results = await asyncio.gather(*probes)
    payload = {
        "study": "FLOW_LAB_LIVE_PUBLIC_FEED_SMOKE",
        "timestamp_ms": int(time.time() * 1000),
        "timeout_sec": timeout_sec,
        "pass": all(r["pass"] for r in results),
        "results": results,
    }
    return payload


def main() -> None:
    p = argparse.ArgumentParser(description="Live public-feed smoke test; no API keys and no orders")
    p.add_argument("--timeout-sec", type=float, default=15.0)
    args = p.parse_args()
    payload = asyncio.run(run(args.timeout_sec))
    print(json.dumps(payload, indent=2, sort_keys=True))
    if not payload["pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

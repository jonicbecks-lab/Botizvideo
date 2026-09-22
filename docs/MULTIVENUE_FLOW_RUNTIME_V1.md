# Flow Lab Runtime v1

## Processes

Run the existing normalized trade collector:

```bash
python -m research.flow_lab.cli collect --assets BTC ETH --output data/flow/live-trades.jsonl
```

Run the microstructure/context collector in parallel:

```bash
python -m research.flow_lab.micro_collector --assets BTC ETH --output-dir data/flow/micro --book-emit-ms 1000
```

The micro collector reads public order books at venue push speed but stores a compact feature snapshot once per configured emit interval. Default is 1000 ms to control storage growth.

Outputs:

- `book_features.jsonl`: best bid/ask, mid, spread, depth within 5/10/25 bps, imbalance and 10-bps additions/removals since the prior emitted snapshot.
- `derivatives_context.jsonl`: public open interest/funding/mark context where available.
- `liquidations.jsonl`: public Bybit liquidation events in v1.
- `health.jsonl`: polling/reconnect failures that must be considered by the Data Quality Gate.

## Venue coverage

Trade flow:

- Binance spot + USD-M perp
- Bybit spot + linear perp
- OKX spot + USDT swap
- Hyperliquid perp

Order book:

- Binance top 20 snapshot stream
- Bybit level 50 snapshot/delta local book
- OKX public `books5`
- Hyperliquid `l2Book`

Derivatives context:

- Binance public open-interest REST polling
- Bybit perp ticker open interest + funding + mark; public all-liquidation stream
- OKX public open-interest REST polling + funding-rate WebSocket
- Hyperliquid `activeAssetCtx` open interest + funding + mark

## Important interpretation rule

`bid_add_usd` / `ask_add_usd` are liquidity-addition proxies, not proof of absorption. A candidate absorption event requires independent aggressive trade flow plus weak price response plus supporting book behavior. Cancellations, quote relocation and feed granularity can otherwise create false positives.

## Historical bootstrap

Binance public daily `aggTrades` archives can be streamed from ZIP without loading an entire day into memory:

```python
from datetime import date
from research.flow_lab.historical import (
    binance_vision_aggtrades_url, download_file, iter_binance_aggtrades_zip,
)

url = binance_vision_aggtrades_url("BTCUSDT", date(2026, 9, 21), "perp")
path = download_file(url, "data/flow/archive/BTCUSDT-2026-09-21.zip")
events = iter_binance_aggtrades_zip(path, "BTCUSDT", "perp")
```

This is the bootstrap source for single-venue historical event studies. Multi-venue conclusions must wait for equivalent historical sources or our own synchronized live accumulation.

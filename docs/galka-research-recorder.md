# Galka Research Recorder

`Galka Research Recorder` is an opt-in research-only sidecar for Galka LIVE. It does not place, cancel, resize, rearm, or close orders and is never consulted by the trading engine when making a live decision.

## Lifecycle

1. A recorder session is armed when a Galka campaign is created or restored from persisted LIVE state.
2. While price is above GALKA, the sidecar subscribes only to public BBO data for that symbol.
3. The first observed BBO mid below `galkaLevel` records `crossed_below_galka` with exchange and local receive timestamps and enables full public `trades` + `l2Book` capture.
4. Full raw capture continues until the Galka campaign is completed/canceled/recovery-closed.
5. If a first `return_to_galka` was observed, lightweight BBO observation may continue for up to five minutes to finish research-only post-return outcome labels. It cannot influence the already-defined live exit.
6. On process shutdown, the writer queue is flushed. On restart, active persisted campaigns are re-armed; a session whose metadata already contains `crossedBelowGalka` resumes full recording.

## Isolation from trading

Market research uses one dedicated public WebSocket connection and a bounded in-memory queue. Disk writes, JSON serialization, feature calculations, checksums, footprint aggregation, and dataset finalization happen on research worker threads. Queue overflow is recorded as `droppedQueueMessages`; the caller is not blocked.

The authenticated Hyperliquid trading gateway, order placement, cancel path, target protection, recovery, SAFE MODE, sizing, and campaign logic are unchanged.

## Local dataset

Default location:

```text
~/.local/share/galka-live/research/galka_campaigns/<SYMBOL>/<campaign_id>/
```

Files:

```text
metadata.json
 events.jsonl
 trades.raw.jsonl
 orderbook.raw.jsonl
 features.jsonl
 footprint.json
 dataset_manifest.json
```

The raw trade and L2 files are append-only JSONL. This deliberately avoids adding a large native Parquet dependency to the Termux trading environment. `dataset_manifest.json` contains file sizes and SHA-256 checksums so the raw files can later be copied to an analysis host and converted to Parquet without changing collection semantics.

## Git storage policy

The existing `data/galka-live-journal` synchronization remains the compact index. It syncs recorder `metadata.json`, `events.jsonl`, `footprint.json`, and `dataset_manifest.json` under `datasets/live/recorder/`.

High-frequency `trades.raw.jsonl`, `orderbook.raw.jsonl`, and `features.jsonl` remain local by default. This avoids turning Git history into a large binary/time-series store while preserving a stable `campaign_id` link to every raw local dataset.

## Raw timestamps

Each raw public-market record stores, where available:

- `exchangeTimestampMs` from Hyperliquid;
- `localReceiveTimestampNs` from the local process clock;
- `localReceiveTimestampMs`;
- approximate `latencyMs`;
- the original public message under `raw`.

Galka executions and lifecycle events are written to the same campaign directory with their campaign IDs and timing metadata.

## Derived research features

Default windows are `100, 250, 500, 1000, 5000, 10000 ms` and are configurable. The recorder calculates research columns only; none are signals.

Features include buy/sell/total aggressive volume, delta, delta change/velocity, CVD, trade rates, volume rate, mean/median/max trade size, quantile-based large-trade threshold, large buy/sell volume, relative volume-spike ratio, order-book imbalance over level counts and bps bands, liquidity additions/removals, replenishment and repeated replenishment.

Final footprint output contains aggressive buy/sell volume, delta, total volume and trade count by price. Configurable diagonal and stacked imbalance calculations are stored as measurements, not trading rules.

## Post-return outcomes

After `return_to_galka`, research metadata can label the subsequent path at:

`+1s, +3s, +5s, +10s, +30s, +60s, +2m, +5m`.

For completed horizons it records endpoint price, MFE, MAE, max/min price, timestamps, and time-to-maximum. These are research labels only and are not available to the live strategy as an exit decision.

## Configuration

The only setting required to enable collection is:

```text
GALKA_RESEARCH_RECORDER_ENABLED=true
```

Optional settings:

```text
GALKA_RESEARCH_RECORDER_DIR=...
GALKA_RESEARCH_L2_DEPTH=20
GALKA_RESEARCH_WINDOWS_MS=100,250,500,1000,5000,10000
GALKA_RESEARCH_FEATURE_INTERVAL_MS=100
GALKA_RESEARCH_BOOK_BPS=1,2.5,5,10,25
GALKA_RESEARCH_IMBALANCE_RATIO=3
GALKA_RESEARCH_STACKED_LEVELS=3
GALKA_RESEARCH_LARGE_TRADE_QUANTILE=0.95
GALKA_RESEARCH_BASELINE_SECONDS=60
GALKA_RESEARCH_FOOTPRINT_PRICE_STEP=0
GALKA_RESEARCH_QUEUE_MAX=50000
```

No UI controls are required for phase 1. The LIVE status response exposes recorder state for diagnostics.

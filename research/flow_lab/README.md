# Multi-Venue Flow Lab v1 — BTC / ETH

Research-only collector, synchronization and event-study layer for signed aggressive trade flow. It does not place orders and requires no exchange API keys.

## Venues

- Binance Spot + USD-M perpetuals
- Bybit Spot + linear perpetuals
- OKX Spot + USDT perpetual swaps
- Hyperliquid perpetuals

Every public trade is normalized to a common `TradeEvent` with exchange, market, BTC/ETH asset, exchange timestamp, price, base quantity, USD notional and aggressing/taker side.

Important: this project calls the measure **signed trade flow** rather than exchange/on-chain netflow. `delta_usd = taker_buy_usd - taker_sell_usd`.

## Run

From repository root:

```bash
python -m pip install -r research/flow_lab/requirements.txt
python -m research.flow_lab.cli collect --assets BTC ETH --output data/flow/live-trades.jsonl
```

The microstructure collector stores order-book, derivatives/OI and liquidation context separately. Once trade and microstructure files exist, build synchronized, quality-gated 30-second pattern windows:

```bash
python -m research.flow_lab.cli build-patterns \
  --trades data/flow/live-trades.jsonl \
  --micro-dir data/flow/micro \
  --output-dir results/flow_lab/synchronized-patterns
```

Run the common catalog event study on those triggers:

```bash
python -m research.flow_lab.cli study-patterns \
  --events results/flow_lab/synchronized-patterns/pattern_events.jsonl \
  --windows results/flow_lab/synchronized-patterns/pattern_windows.jsonl \
  --output-dir results/flow_lab/catalog-study
```

For a simple stored-flow inspection without the catalog pipeline:

```bash
python -m research.flow_lab.cli summarize --input data/flow/live-trades.jsonl --window 30 --tail 20
```

## Pattern catalog

Flow Lab is not an absorption-only model. The current research catalog contains independent pattern families:

- **Absorption:** extreme signed flow with unusually weak volatility-normalized price response.
- **Continuation:** extreme flow with strong same-direction price response.
- **Instant reversal:** price is already moving materially against the extreme aggressive flow; kept separate from absorption.
- **Exhaustion:** flow intensity decays after an observed extreme while price stops progressing; trigger occurs only after the decay is visible.
- **Cross-venue consensus/divergence:** equal-weight, past-normalized venue scores confirm one another or materially oppose one another.
- **Spot/perp divergence and lead:** standardized spot and derivatives flow disagree, confirm, or one side moves while the other remains muted.
- **OI positioning context:** price + OI + aggressive-flow combinations are descriptive labels such as new-long build, new-short build, or unwind. They are not treated as causal truth.
- **Liquidation regimes:** liquidation notional must itself be extreme versus prior history; a liquidation-driven candidate additionally requires price and aggressive flow to align with the forced side.
- **Order-book absorption evidence:** replenishment/depletion and weak price impact are retained as microstructure evidence, not proof of hidden intent.

`PatternEngine` evaluates these families only after the point-in-time Data Quality Gate passes. `PatternFamilyClusterer` then applies the same causal first-event / two-minute cooldown rule to every emitted pattern so adjacent windows do not inflate the independent event count.

## Common event study

`catalog_study.py` evaluates all catalog families with the same machinery:

- 30 s, 1 m, 2 m, 5 m, 15 m, 30 m and 1 h forward horizons;
- raw and preregistered-direction returns kept separate;
- mean, median and hit rate;
- close-path MFE/MAE on completed quality-passed windows;
- UTC-day block bootstrap confidence intervals;
- UTC-day block sign-flip tests;
- Benjamini-Hochberg FDR across catalog groups and horizons;
- 0 / 2 / 5 bps cost-sensitivity scenarios;
- raw event count and independent family count both preserved.

## Scientific rules

1. Never use a fixed raw-dollar flow threshold as the primary event definition.
2. Score current flow against **past completed windows only** using rolling percentile / z-score.
3. Keep BTC and ETH results separate before pooled analysis.
4. Keep spot and perpetual flow separate before optional composite analysis.
5. Do not assign discretionary venue weights. Cross-venue composites use equal-weight past-normalized venue scores.
6. Future returns are labels only. They must never enter decision-time features.
7. Stateful baselines are updated only after the corresponding point-in-time Data Quality Gate passes.
8. Preserve raw event counts and independent event-family counts; the first causal event, never a hindsight-selected strongest event, represents a family.
9. No strategy promotion from descriptive correlation. Require chronological walk-forward and untouched holdout validation after costs and multiple-testing correction.
10. Thresholds are frozen before a new holdout is inspected; a viewed holdout is never reused as fresh OOS evidence.

## Validation boundaries

Historical Binance-only archives can evaluate response families such as absorption, continuation and instant reversal. They **cannot** validate cross-venue, spot/perp, OI, liquidation or order-book hypotheses by themselves; those require synchronized multi-source data.

The live pattern smoke workflow deliberately uses a short warm-up (`min_history=2`, 90th percentile) only to verify end-to-end plumbing. Its output is explicitly marked `smoke_only=true` and **must not** be used as evidence of predictive edge or for parameter selection.

Pattern discovery and evaluation remain research-only until sufficiently large chronological walk-forward samples show stable out-of-sample behavior after realistic costs and multiple-testing controls. PAPER execution is outside this module and is not modified by Flow Lab.

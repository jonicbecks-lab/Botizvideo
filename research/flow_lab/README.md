# Multi-Venue Flow Lab v1 — BTC / ETH

Research-only collector and feature layer for signed aggressive trade flow. It does not place orders and requires no exchange API keys.

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

Aggregate a stored stream into 30-second venue buckets:

```bash
python -m research.flow_lab.cli summarize --input data/flow/live-trades.jsonl --window 30 --tail 20
```

## Scientific rules

1. Never use a fixed raw-dollar flow threshold as the primary event definition.
2. Score current flow against **past completed windows only** using rolling percentile / z-score.
3. Keep BTC and ETH results separate before pooled analysis.
4. Keep spot and perpetual flow separate before optional composite analysis.
5. Do not assign discretionary venue weights in v1. Learn venue lead/lag and marginal predictive value out of sample.
6. Future returns are labels only. They must never enter decision-time features.
7. No strategy promotion from descriptive correlation. Require chronological walk-forward and untouched holdout validation after costs.
8. Preserve raw event counts and independent event-family counts; do not inflate evidence with adjacent windows from one episode.

## Pattern catalog

Flow Lab is not an absorption-only model. The current research catalog contains independent pattern families:

- **Absorption:** extreme signed flow with unusually weak volatility-normalized price response.
- **Continuation:** extreme flow with strong same-direction price response.
- **Instant reversal:** price is already moving materially against the extreme aggressive flow; kept separate from absorption.
- **Exhaustion:** flow intensity decays after an observed extreme while price stops progressing; trigger is causal and occurs only after decay is visible.
- **Cross-venue consensus/divergence:** equal-weight, past-normalized venue scores confirm one another or materially oppose one another.
- **Spot/perp divergence and lead:** standardized spot and derivatives flow disagree, confirm, or one side moves while the other remains muted.
- **OI positioning context:** price + OI + aggressive-flow combinations are descriptive labels such as new-long build, new-short build, or unwind. They are not treated as causal truth.
- **Liquidation regimes:** liquidation notional must itself be extreme versus prior history; a liquidation-driven candidate additionally requires price and aggressive flow to align with the forced side.
- **Order-book absorption evidence:** replenishment/depletion and weak price impact are retained as microstructure evidence, not proof of hidden intent.

`patterns.py` contains the common pattern classifiers. `pattern_study.py` attaches the same forward-return labels to all triggered pattern families so they can be compared with one methodology instead of cherry-picking different metrics for each pattern.

## Validation policy

- Thresholds are frozen before inspecting a new holdout.
- Historical Binance-only studies can validate response families such as absorption/continuation/reversal, but **cannot** validate cross-venue, spot/perp, OI or liquidation hypotheses by themselves.
- Multi-venue/OI/liquidation patterns require time-aligned live or archived data from the relevant venues.
- Pattern discovery and evaluation remain research-only until a walk-forward study shows stable out-of-sample behavior after realistic costs.

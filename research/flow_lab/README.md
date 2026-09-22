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

## Candidate hypotheses to test

- **Absorption:** extreme signed flow but unusually weak price response in that direction.
- **Continuation:** extreme signed flow and strong price response in the same direction.
- **Exhaustion:** flow intensity decays after an extreme while price stops progressing.
- **Cross-venue consensus:** the same flow direction appears on multiple independent venues.
- **Lead/lag:** one venue's signed flow systematically precedes other venues' price/flow response.
- **Spot/perp divergence:** spot and derivatives aggressive flow disagree.

Order-book replenishment and open-interest context are the next research layers. They should be joined by exchange timestamp and tested as incremental features rather than assumed filters.

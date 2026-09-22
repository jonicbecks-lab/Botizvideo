# BTC/ETH Multi-Venue Flow Research v1

## Objective

Test whether public aggressive trade flow across major BTC/ETH venues contains stable, causal information about short-horizon price behavior, especially absorption, continuation, exhaustion, cross-venue consensus and lead/lag.

The target is not a hand-tuned signal. The target is an out-of-sample mapping from market state at time *t* to the distribution of future returns and adverse/favorable excursion.

## Data contract

Raw normalized trade event:

- exchange, market, asset, symbol
- exchange event/trade timestamp and optional receive timestamp
- price and base quantity
- USD notional
- taker/aggressor side
- exchange trade identifier

Derived windows: 1s, 5s, 15s, 30s, 60s, 5m and 15m. Initial statistical work should favor 5s–60s for microstructure and retain slower windows as context.

## Primary variables

For each exchange/market/window:

- taker buy USD
- taker sell USD
- delta USD = buy - sell
- gross USD = buy + sell
- flow ratio = delta / gross
- CVD / rolling cumulative delta
- return, high-low range, realized volatility
- response in flow direction
- rolling past-only delta percentile and z-score

Cross-venue state:

- aggregate buy/sell/delta/gross
- number of venues agreeing with direction
- consensus fraction
- venue concentration of gross flow
- spot vs perp divergence
- lagged venue-specific flows

## Event studies

For each event time, record future outcomes at 5s, 15s, 30s, 1m, 2m, 5m, 15m, 30m and 1h:

- signed and raw return
- MFE / MAE
- time to local reversal / continuation
- subsequent flow state

Do not define a trade yet. First estimate conditional outcome distributions.

## Hypothesis families

### H1 — Absorption
Extreme negative flow + weak/positive price response may indicate passive bid absorption. Mirror for positive flow at asks.

### H2 — Continuation
Extreme flow + efficient price movement in the same direction may identify true initiative flow rather than absorption.

### H3 — Exhaustion
After an extreme, falling flow intensity plus loss of price progress may precede mean reversion.

### H4 — Cross-venue confirmation
An extreme supported by multiple venues may differ from a one-venue anomaly.

### H5 — Lead/lag
Venue A flow at t may improve prediction of Venue B / composite return at t + lag. Test a grid of lags with multiple-testing control and year/regime stability.

### H6 — Spot/perp divergence
Spot-led and perp-led moves can have different continuation/reversal distributions. Treat direction and venue type as features, not assumptions.

## Anti-leakage / validation

- UTC/exchange timestamps; retain receive timestamp for latency diagnostics.
- causal rolling features only; no centered windows.
- deduplicate by venue + trade ID.
- chronological train/discovery/confirmation/frozen split.
- purge/embargo overlapping event families.
- choose thresholds on discovery, not frozen data.
- compare against unconditional and simple flow-ratio baselines.
- report sample size, confidence interval, costs, year/regime stability and sensitivity to neighboring thresholds.

## Next layers

1. Top-of-book and depth/replenishment: executed flow versus disappearing/refilling liquidity.
2. Open interest / funding / liquidations to distinguish new positioning, closing and forced flow.
3. Only after those layers are validated, feed the stable features into the existing BTC/ETH PAPER meta-controller as a separate expert; do not silently modify the existing strategy.

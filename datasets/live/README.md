# Galka LIVE research dataset

This branch stores non-secret research telemetry copied from the on-device Galka LIVE engine.

The dataset is intentionally separated from the trading-code branch. It must never contain wallet private keys, browser session tokens, signing material, account configuration, or `.env` files.

## Layout

- `datasets/live/manifest.json` — schema and research purpose.
- `datasets/live/campaigns/<campaignId>.json` — latest complete snapshot for each GALKA campaign.
- `datasets/live/events.jsonl` — chronological campaign events.
- `datasets/live/fills.jsonl` — exchange fills with ownership classification, prices, sizes, fees and closed PnL.

Campaign snapshots preserve ladder geometry, GALKA price, placement-market distance when available, level fills, L1 rearm cycles, deepest level, realized PnL fields, recorded fees, recovery state and timestamps.

These timestamps and prices are sufficient to join the executions later with public historical market data and calculate post-exit MFE/MAE at 1m, 5m, 15m, 30m, 1h, 4h and longer horizons. That is the basis for testing whether immediate exit at GALKA should remain the default or whether specific setup classes benefit from trailing or delayed exits.

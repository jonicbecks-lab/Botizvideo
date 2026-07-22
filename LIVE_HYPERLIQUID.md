# Galka Hyperliquid LIVE — Hardened v3

## Trading contract

- Hyperliquid perpetuals: BTC, ETH, SOL.
- Long only, manual GALKA price.
- One active LIVE campaign across all supported coins.
- 1–10x isolated margin only.
- Eight ALO/post-only entries below GALKA: 0.15%, 0.30%, 0.45%, 0.60%, 0.90%, 1.20%, 1.50%, 2.00%.
- Every entry is submitted with an exchange-native reduce-only trigger-limit TP at GALKA.
- If only L1 participates and closes by an owned TP, L1 may be rearmed.
- If L2 or deeper participates and the owned position closes, remaining owned orders are canceled and the campaign completes.
- Unknown/manual fills, state mismatch or exchange ambiguity disable automatic rearm and enter recovery/SAFE MODE.
- No strategy stop-loss and no automatic campaign expiry.

## Allocation and margin

The live ladder enforces the exchange minimum order notional after lot-size rounding. It starts from the configured weights, raises small levels to the minimum, and reduces larger levels so the total does not exceed the requested notional. A campaign is rejected when eight valid orders do not fit.

The engine also rejects a campaign when its estimated initial margin exceeds `HL_MAX_MARGIN_FRACTION` of current withdrawable funds. This is a placement guard, not a guarantee against liquidation or later margin changes.

## Secret handling

The browser never receives the API Wallet private key. It is read only by the Termux Python process from:

```text
~/.config/galka-live.env
```

Required mode:

```bash
chmod 600 ~/.config/galka-live.env
```

Use the private key of an approved API Wallet / Agent Wallet, not the main-wallet seed phrase or private key.

## Setup and verification

```bash
cd ~/GalkaLive
bash scripts/setup-galka-live.sh
bash scripts/verify-galka-live.sh
```

Keep LIVE disabled for the online read-only check:

```text
HL_LIVE_ENABLED=NO
HL_LIVE_CONFIRM=NOT_CONFIRMED
```

```bash
bash scripts/check-galka-live-account.sh
bash scripts/start-galka-live.sh
```

The local server binds only to `127.0.0.1`. Each launch creates a random browser session token. The static server exposes only the terminal assets, not Python source, state or configuration files.

## Runtime safety behavior

- Initial venue reconciliation completes before the browser is served as ready.
- New campaigns are refused when any BTC/ETH/SOL position/order already exists.
- Every exchange response is checked at both top-level and per-order status level.
- Cancellation and emergency close require repeated fresh venue confirmation.
- Actual exchange position is compared with locally owned fills.
- Recovery cancels owned entries while keeping/repairing reduce-only target coverage.
- Corrupt state, foreign orders, unknown position, unexpected short, monitor failure or API ambiguity enable SAFE MODE.
- SAFE MODE blocks new campaigns until an explicit clean reconcile.
- Native exchange TP protects accepted entries while Termux is paused, but local rearm/reconciliation requires the process to be running.

## Promotion checklist

1. `bash scripts/verify-galka-live.sh` passes in Termux.
2. `bash scripts/check-galka-live-account.sh` identifies the correct account and reports expected clean state.
3. READ ONLY terminal shows correct network, balance, mids and candles.
4. A controlled minimal-notional preview produces eight valid orders and acceptable margin.
5. A real smoke test verifies entry, native TP, fill ownership and cleanup in the official Hyperliquid interface.
6. L1 rearm, L2+ completion, restart recovery, cancel and emergency-close paths are exercised deliberately.
7. Only after repeated clean cycles should notional be increased in steps.

See `HARDENED_LIVE_README_RU.md` for the detailed rollout and limitations.

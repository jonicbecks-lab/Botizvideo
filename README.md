# Galka Pro

Galka Pro is a mobile-first, **paper-only** terminal for one long-only workflow: choose a GALKA
level, build a limit ladder below it, observe fills, reclaim the level, and exit through a trailing
stop. Radar is an explainable visual assistant and never opens trades.

The primary application is [`terminal/pro.html`](terminal/pro.html). It runs directly in a browser
without a build step, exchange keys, or server infrastructure.

## What is included

- public Binance USD-M Futures data for BTCUSDT, ETHUSDT, and SOLUSDT;
- simultaneous paper engines for all three instruments;
- manual GALKA placement by chart tap, exact price, or drag before the first fill;
- safe pre-trade preview with first/last limit, step count, notional, full-fill average, and an
  estimated return-to-GALKA PnL;
- compact ladder states (`WAIT`, `FILLED`, `CANCELLED`), average entry, reclaim, trailing stop,
  expiry, and PnL;
- explainable Radar score with strength filters, visible-range mode, clustering, candidate detail,
  and positive/negative training labels;
- touch-safe drawing tools with selection, move, handles, properties, duplicate, lock, undo/redo,
  and delete;
- Session Health, deterministic 1m paper replay after background/reconnect, activity log, safe backup/restore,
  onboarding, training Replay, and installable PWA shell;
- responsive layouts for 360–430 px portrait, Android landscape, Samsung DeX, and desktop.

Galka Pro does **not** place real orders. The service worker caches interface files only and does not
pretend to keep the paper engine alive when Android freezes the browser tab.

## Data safety

All user state stays in browser localStorage under the unchanged key:

```text
galka-pro-v1
```

Store migration is additive and tested. Existing BTC/ETH/SOL campaigns, pending and filled limits,
open positions, trailing state, history, settings, drawings, templates, alerts, manual examples, and
Radar labels are retained. Full snapshot import validates and previews the file, then creates a
backup of current state before restore.

See [`GALKA_CONSTITUTION.md`](GALKA_CONSTITUTION.md) for the safety contract and
[`docs/GALKA_DESIGN_SYSTEM.md`](docs/GALKA_DESIGN_SYSTEM.md) for the UI system. The explicit
reconnect assumptions are documented in
[`docs/GALKA_PAPER_RECOVERY.md`](docs/GALKA_PAPER_RECOVERY.md).

## Start in Termux

First installation:

```bash
pkg update -y
pkg install git python -y
git clone https://github.com/CryptoJonic/MeteoraAgent.git ~/Galka
cd ~/Galka
bash scripts/start-termux.sh
```

Existing installation after changes reach `main`:

```bash
cd ~/Galka
git pull --ff-only origin main
bash scripts/start-termux.sh
```

The launcher chooses a free port from 8080–8089 and opens a cache-busted `terminal/pro.html` URL.
It never switches branches, resets files, or touches browser data.

## Desktop / DeX launch

```bash
python -m http.server 8080
```

Open `http://127.0.0.1:8080/terminal/pro.html`.

## Architecture

- `terminal/pro.html` — semantic shell, sheets, modals, PWA metadata;
- `terminal/pro.css` — design tokens and portrait/landscape/DeX/desktop layouts;
- `terminal/pro.js` — market/chart adapter and UI orchestration;
- `terminal/modules/store.js` — defaults, additive migrations, localStorage, activity;
- `terminal/modules/paper-engine.js` — deterministic ladder and quote processing, independent of DOM;
- `terminal/modules/radar-engine.js` — explainable scoring and filtering, independent of paper state;
- `terminal/modules/backup.js` — full snapshot validation and summaries;
- `terminal/sw.js`, `terminal/manifest.webmanifest`, `terminal/icons/` — installable PWA shell;
- `scripts/start-termux.sh` — one-command Android launcher;
- `scripts/check-pro-terminal.mjs` and `scripts/test-*.mjs` — architecture and invariant checks.

Research and older audit terminals remain in `research/`, `results/`, `terminal/index.html`,
`terminal/live.html`, and `terminal/backtest.html`.

## Validation

Requires Node.js 20+ for development checks only. Runtime use in Termux does not require Node or
`npm install`.

```bash
npm run check
```

The suite covers syntax/static contracts, store migration and localStorage round-trip, preservation
of active campaigns, three simultaneous instruments, live and reconnect fill idempotency,
deterministic 1m recovery, boundary-candle safety, reclaim/trailing invariants,
Radar visual-only behavior, positive/negative labels, PWA files, accessibility, and responsive
contracts for 360×800, 390×844, 844×390, and 1440×900.

## Hyperliquid LIVE hardened build

Operational instructions and safety limitations: `HARDENED_LIVE_README_RU.md`.

# Hyperliquid LIVE implementation status

- Build: Hardened v3, 2026-07-22.
- Real-order path: implemented, fail-closed, disabled by default.
- Scope: BTC/ETH/SOL perpetuals, long-only GALKA, one active campaign, 1–10x isolated.
- Local verification: 44 Python LIVE tests and all repository JavaScript/static checks pass in the audit environment.
- Remaining promotion requirement: Termux verification against the user's pinned SDK, read-only account check, and controlled small-notional real smoke tests.
- Do not treat this status as a guarantee against exchange outages, liquidation, strategy loss, Android process termination, or unknown production conditions.

# Galka App read-only API

The mobile boundary is opt-in and loopback-only. `GALKA_APP_READ_ONLY_TOKEN` is loaded from the same mode-0600 server config as the other local settings, but is compared only by the `/api/app/*` GET dispatcher. It is never accepted by `/api/live/*`.

## Routes

- `GET /api/app/snapshot` — one projection of Engine status and cached open orders.
- `GET /api/app/candles?coin=BTC&interval=15m&limit=300&from=...&to=...` — the existing Hyperliquid candle reader, sorted, deduplicated and validated.
- `GET /api/app/events?since=...&limit=50` — sanitized Engine events without metadata or internal errors.

Authentication uses `X-Galka-App-Token`. Missing or invalid credentials return `401`; invalid parameters return `400`; rate limits return `429`; internal failures return a generic `500`. No other app routes exist. `POST`, `PUT`, `PATCH`, and `DELETE` under `/api/app/*` return `405` before reading a request body or dispatching an Engine method.

## Snapshot boundary

The presenter only selects and renames values already provided by `engine.status()` and `gateway.open_orders()`. Campaign IDs, public level state, position/account figures and public order fields are exposed. CLOIDs, internal ownership maps, event metadata, agent/account addresses, stack traces, keys and signing data are omitted.

`SAFE_MODE` is observational. Reading the endpoint does not reconcile, clear SAFE MODE, mutate local state or call exchange writes.

## Network and CORS

The production bind remains `127.0.0.1`. A native Expo client on the same Android phone can use loopback without exposing Termux to the LAN. Requests without `Origin` are accepted after token validation. Browser origins are restricted to the Engine's loopback origin plus the optional loopback-only `GALKA_APP_ALLOWED_ORIGIN`; wildcard CORS is never emitted. Existing browser/trading routes retain their original same-origin policy.

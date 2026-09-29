# Dev Control contract — GALKA LIVE

This file is the operational contract for chats/agents that modify the active Hyperliquid GALKA application.

## Fixed target

- Repository: `jonicbecks-lab/Botizvideo`
- Working branch: `agent/galka-three-live-campaigns`
- Termux checkout: normally `~/GalkaLive`
- Start command: `bash scripts/galka-live-start.sh`
- Status command: `bash scripts/galka-live-status.sh`
- Safe updater: `scripts/galka-live-apply-update.sh`
- Browser URL/token is resolved by Dev Control from `scripts/galka-live-common.sh`; do not hard-code or expose the session token.

Do not mix GALKA LIVE with Galka Lab, Galka Demo, Meteora, or other experiments when preparing a Dev Control update.

## Required workflow for every code change

1. Inspect the real current branch and relevant files before editing.
2. Make the requested change on `agent/galka-three-live-campaigns`.
3. Run the appropriate tests/validation before presenting the update as ready. For changes that affect GALKA LIVE, `bash scripts/verify-galka-live.sh` is the canonical local validation path when the environment supports it.
4. Commit/push the finished code to `agent/galka-three-live-campaigns`.
5. Do not ask the user to open Termux, run `git pull`, install Python packages, restart GALKA, or manually `cd` into the repo during the normal path.
6. End with exactly one copyable Dev Control update block:

```bash
# DEVCONTROL: GALKA
set -euo pipefail
bash scripts/galka-live-apply-update.sh
```

The first non-empty line must remain exactly `# DEVCONTROL: GALKA`, otherwise Dev Control will reject it.

## Update safety

`scripts/galka-live-apply-update.sh` is the source of truth for routine deployment. Do not replace it with ad-hoc `git pull`/`pip install`/restart commands.

The updater is designed to:

- refuse tracked local changes and unsafe branch divergence;
- fetch only the fixed GALKA branch;
- preflight the remote commit in a temporary worktree before touching the running GALKA checkout;
- validate the Python dependency lock and GALKA verification suite;
- remember whether GALKA was running before the update;
- stop the local monitor only after preflight succeeds;
- fast-forward only;
- rebuild the live venv only when required;
- restart only if GALKA was running before the update;
- health-check the restarted server;
- roll code/dependencies back to the previous commit if the swap or restart fails.

Stopping the local monitor does not cancel exchange orders. Therefore agents should keep the update path short, avoid unnecessary dependency changes, and never add unrelated long-running work after the monitor has been stopped.

Secrets and the external GALKA config must never be printed, copied into update scripts, committed, or requested from the user.

## Logs and troubleshooting

When runtime evidence is needed, ask the user to tap `GALKA LOG` in Dev Control and paste the copied result. Do not ask for manual Termux log commands unless Dev Control itself is broken and the normal log path is unavailable.

Dev Control includes the GALKA server status/log and the last Dev Control UPDATE transcript, so update failures should normally be diagnosable from `GALKA LOG`.

## OPEN behavior

`GALKA` in Dev Control owns normal start/open behavior. Feature work must remain compatible with `scripts/galka-live-start.sh`, `scripts/galka-live-common.sh`, local-only binding, health checks, and protected browser session tokens.

Do not weaken the localhost/session-token/security model to make launching easier.

## Agent response rule

After implementation, report briefly what changed and what validation passed, then provide the standard update block. Do not provide several alternative installation paths unless the standard updater itself is unavailable and recovery is explicitly required.

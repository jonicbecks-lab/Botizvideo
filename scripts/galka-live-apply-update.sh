#!/data/data/com.termux/files/usr/bin/bash
set -Eeuo pipefail

ROOT="${GALKA_REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
BRANCH="${GALKA_UPDATE_BRANCH:-agent/galka-three-live-campaigns}"
VENV="$ROOT/.venv-live"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)-$$"
TMP_ROOT="$(mktemp -d "${TMPDIR:-${PREFIX:-/data/data/com.termux/files/usr}/tmp}/galka-update.XXXXXX")"
WORKTREE="$TMP_ROOT/worktree"
OLD_HEAD=""
REMOTE_HEAD=""
WAS_RUNNING=0
VENV_BACKUP=""
CODE_UPDATED=0

# shellcheck disable=SC2317
cleanup() {
  set +e
  if [[ -d "$WORKTREE" ]]; then
    git -C "$ROOT" worktree remove --force "$WORKTREE" >/dev/null 2>&1 || true
  fi
  rm -rf "$TMP_ROOT" >/dev/null 2>&1 || true
}
trap cleanup EXIT

cd "$ROOT"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "GALKA UPDATE: $ROOT is not a Git repository" >&2
  exit 2
fi

CURRENT_BRANCH="$(git branch --show-current)"
if [[ "$CURRENT_BRANCH" != "$BRANCH" ]]; then
  echo "GALKA UPDATE aborted: expected branch $BRANCH, current branch is $CURRENT_BRANCH" >&2
  exit 3
fi

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "GALKA UPDATE aborted: tracked files have local changes. Commit or restore them first." >&2
  git status --short --untracked-files=no >&2 || true
  exit 4
fi

OLD_HEAD="$(git rev-parse HEAD)"
echo "=== GALKA SAFE UPDATE $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
echo "branch=$BRANCH old_head=$OLD_HEAD"

echo "[1/6] Fetching remote branch..."
git fetch origin "$BRANCH"
REMOTE_HEAD="$(git rev-parse "origin/$BRANCH")"
echo "remote_head=$REMOTE_HEAD"

if [[ "$REMOTE_HEAD" == "$OLD_HEAD" ]]; then
  echo "GALKA UPDATE: already on latest version; no restart required."
  exit 0
fi

if ! git merge-base --is-ancestor "$OLD_HEAD" "$REMOTE_HEAD" >/dev/null 2>&1; then
  echo "GALKA UPDATE aborted: local branch cannot fast-forward safely to origin/$BRANCH" >&2
  exit 5
fi

new_requirements_hash="$(git show "$REMOTE_HEAD:live/requirements-termux.txt" | sha256sum | awk '{print $1}')"

echo "[2/6] Preflight-testing the new commit while current GALKA keeps running..."
git worktree add --detach "$WORKTREE" "$REMOTE_HEAD" >/dev/null

marker=""
if [[ -f "$VENV/.galka-requirements-sha256" ]]; then
  marker="$(<"$VENV/.galka-requirements-sha256")"
fi

if [[ -x "$VENV/bin/python" && "$marker" == "$new_requirements_hash" ]]; then
  ln -s "$VENV" "$WORKTREE/.venv-live"
else
  echo "Preflight requires a temporary dependency environment."
  (
    cd "$WORKTREE"
    bash scripts/setup-galka-live.sh --dependencies-only
  )
fi

if ! (
  cd "$WORKTREE"
  bash scripts/verify-galka-live.sh
); then
  echo "GALKA UPDATE aborted: preflight validation failed. Running installation was not touched." >&2
  exit 6
fi

# Remove preflight worktree before changing the live checkout.
git worktree remove --force "$WORKTREE" >/dev/null

echo "[3/6] Detecting live server state..."
if bash scripts/galka-live-status.sh >/dev/null 2>&1; then
  WAS_RUNNING=1
  echo "GALKA server is running; it will be restarted only after preflight passed."
else
  echo "GALKA server is currently stopped; update will keep it stopped."
fi

rollback() {
  local reason="$1"
  set +e
  echo "ROLLBACK: $reason" >&2

  if [[ -x "$VENV/bin/python" && -f scripts/galka-live-stop.sh ]]; then
    bash scripts/galka-live-stop.sh STOP_GALKA_LIVE >/dev/null 2>&1 || true
  fi

  if [[ "$CODE_UPDATED" -eq 1 && -n "$OLD_HEAD" ]]; then
    git reset --hard "$OLD_HEAD" >/dev/null 2>&1 || true
  fi

  if [[ -n "$VENV_BACKUP" && -d "$VENV_BACKUP" ]]; then
    rm -rf "$VENV" >/dev/null 2>&1 || true
    mv "$VENV_BACKUP" "$VENV" >/dev/null 2>&1 || true
  fi

  if [[ "$WAS_RUNNING" -eq 1 ]]; then
    GALKA_LIVE_NO_OPEN=1 bash scripts/galka-live-start.sh >/dev/null 2>&1 || true
    if bash scripts/galka-live-status.sh >/dev/null 2>&1; then
      echo "ROLLBACK OK: previous commit restored and GALKA is running again." >&2
    else
      echo "ROLLBACK WARNING: previous commit restored, but GALKA health did not recover. Use GALKA LOG." >&2
    fi
  else
    echo "ROLLBACK OK: previous commit restored; GALKA remains stopped as before." >&2
  fi
  exit 1
}

if [[ "$WAS_RUNNING" -eq 1 ]]; then
  echo "[4/6] Stopping GALKA for the shortest possible swap window..."
  if ! bash scripts/galka-live-stop.sh STOP_GALKA_LIVE; then
    echo "GALKA UPDATE aborted: could not stop the local monitor cleanly." >&2
    exit 7
  fi
else
  echo "[4/6] No running server to stop."
fi

echo "[5/6] Fast-forwarding code and validating dependencies..."
if ! git merge --ff-only "origin/$BRANCH"; then
  rollback "fast-forward failed"
fi
CODE_UPDATED=1

# Rebuild the live venv only when the lock changed or the existing environment is invalid.
need_dependencies=0
marker=""
if [[ -f "$VENV/.galka-requirements-sha256" ]]; then
  marker="$(<"$VENV/.galka-requirements-sha256")"
fi
if [[ ! -x "$VENV/bin/python" || "$marker" != "$new_requirements_hash" ]]; then
  need_dependencies=1
elif ! "$VENV/bin/python" -c 'import hyperliquid, eth_account, eth_utils' >/dev/null 2>&1; then
  need_dependencies=1
fi

if [[ "$need_dependencies" -eq 1 ]]; then
  if [[ -d "$VENV" ]]; then
    VENV_BACKUP="$ROOT/.venv-live.devcontrol-backup-$STAMP"
    mv "$VENV" "$VENV_BACKUP"
  fi
  if ! bash scripts/setup-galka-live.sh --dependencies-only; then
    rollback "dependency installation failed"
  fi
fi

if ! "$VENV/bin/python" scripts/check-python-lock.py; then
  rollback "dependency lock validation failed"
fi
if ! PYTHONPATH="$ROOT" "$VENV/bin/python" -m compileall -q live; then
  rollback "Python compile validation failed"
fi

echo "[6/6] Restoring previous run state and checking health..."
if [[ "$WAS_RUNNING" -eq 1 ]]; then
  if ! GALKA_LIVE_NO_OPEN=1 bash scripts/galka-live-start.sh; then
    rollback "new GALKA server failed to start"
  fi
  if ! bash scripts/galka-live-status.sh >/dev/null 2>&1; then
    rollback "new GALKA server failed health check"
  fi
  echo "GALKA UPDATE SUCCESS: updated to $(git rev-parse HEAD) and server health is OK."
else
  echo "GALKA UPDATE SUCCESS: updated to $(git rev-parse HEAD); server remains stopped because it was stopped before update."
fi

if [[ -n "$VENV_BACKUP" && -d "$VENV_BACKUP" ]]; then
  rm -rf "$VENV_BACKUP"
fi

exit 0

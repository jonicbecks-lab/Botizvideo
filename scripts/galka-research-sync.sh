#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/galka-live-common.sh"

RESEARCH_ROOT="$GALKA_LIVE_DATA_DIR/research"
CAMPAIGN_SRC="$RESEARCH_ROOT/campaigns"
SYNC_ROOT="${XDG_CACHE_HOME:-$HOME/.cache}/galka-research-sync"
REMOTE_BRANCH="data/galka-live-journal"
REMOTE_URL="https://github.com/jonicbecks-lab/Botizvideo.git"
DATASET_DIR="$SYNC_ROOT/datasets/live"

[[ "${1:-}" == "--once" ]] || {
  echo "Usage: bash scripts/galka-research-sync.sh --once"
  exit 2
}

# Research sync is deliberately best-effort and must never affect trading.
[[ -d "$RESEARCH_ROOT" ]] || exit 0
mkdir -p "$(dirname "$SYNC_ROOT")"

if [[ ! -d "$SYNC_ROOT/.git" ]]; then
  rm -rf "$SYNC_ROOT"
  # Use a canonical URL rather than copying origin from the trading checkout:
  # an origin URL can legally contain credentials and must never be persisted
  # into the research cache clone.
  git clone --no-checkout "$REMOTE_URL" "$SYNC_ROOT" >/dev/null 2>&1 || exit 0
fi

git -C "$SYNC_ROOT" remote set-url origin "$REMOTE_URL" >/dev/null 2>&1 || exit 0
git -C "$SYNC_ROOT" fetch -q origin "$REMOTE_BRANCH" || exit 0
git -C "$SYNC_ROOT" checkout -q -B galka-research-sync "origin/$REMOTE_BRANCH" || exit 0

mkdir -p "$DATASET_DIR/campaigns"
if [[ -f "$RESEARCH_ROOT/manifest.json" ]]; then
  cp -f "$RESEARCH_ROOT/manifest.json" "$DATASET_DIR/manifest.json"
fi
if [[ -f "$RESEARCH_ROOT/events.jsonl" ]]; then
  cp -f "$RESEARCH_ROOT/events.jsonl" "$DATASET_DIR/events.jsonl"
fi
if [[ -f "$RESEARCH_ROOT/fills.jsonl" ]]; then
  cp -f "$RESEARCH_ROOT/fills.jsonl" "$DATASET_DIR/fills.jsonl"
fi
if [[ -d "$CAMPAIGN_SRC" ]]; then
  find "$CAMPAIGN_SRC" -maxdepth 1 -type f -name '*.json' -exec cp -f {} "$DATASET_DIR/campaigns/" \;
fi

# Refuse to publish obvious secret-shaped files even if somebody accidentally
# places one under the local research directory later.
if find "$DATASET_DIR" -type f \( -iname '*key*' -o -iname '*secret*' -o -iname '*token*' -o -iname '*.env' \) | grep -q .; then
  exit 0
fi

# Dataset files contain only research telemetry. The live config/state/runtime
# directories are never copied into this clone.
git -C "$SYNC_ROOT" add datasets/live
if git -C "$SYNC_ROOT" diff --cached --quiet; then
  exit 0
fi

git -C "$SYNC_ROOT" -c user.name='Galka Research Sync' -c user.email='galka-research@local' \
  commit -q -m "Sync Galka research dataset $(date -u +%Y-%m-%dT%H:%M:%SZ)" || exit 0

git -C "$SYNC_ROOT" push -q origin HEAD:"$REMOTE_BRANCH" || exit 0

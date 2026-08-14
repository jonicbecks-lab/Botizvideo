#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/galka-live-common.sh"

RESEARCH_ROOT="$GALKA_LIVE_DATA_DIR/research"
CAMPAIGN_SRC="$RESEARCH_ROOT/campaigns"
RECORDER_SRC="$RESEARCH_ROOT/galka_campaigns"
SYNC_ROOT="${XDG_CACHE_HOME:-$HOME/.cache}/galka-research-sync"
REMOTE_BRANCH="data/galka-live-journal"
REMOTE_URL="https://github.com/jonicbecks-lab/Botizvideo.git"
DATASET_DIR="$SYNC_ROOT/datasets/live"
RECORDER_DST="$DATASET_DIR/recorder"
LOCK_DIR="${SYNC_ROOT}.lock"

[[ "${1:-}" == "--once" ]] || {
  echo "Usage: bash scripts/galka-research-sync.sh --once"
  exit 2
}

# Research sync is deliberately best-effort and must never affect trading.
[[ -d "$RESEARCH_ROOT" ]] || exit 0
mkdir -p "$(dirname "$SYNC_ROOT")"

# Prevent two sync jobs on the same device from racing each other.
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  exit 0
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT

if [[ ! -d "$SYNC_ROOT/.git" ]]; then
  rm -rf "$SYNC_ROOT"
  # Use a canonical URL rather than copying origin from the trading checkout:
  # an origin URL can legally contain credentials and must never be persisted
  # into the research cache clone.
  git clone --no-checkout "$REMOTE_URL" "$SYNC_ROOT" >/dev/null 2>&1 || exit 0
fi

git -C "$SYNC_ROOT" remote set-url origin "$REMOTE_URL" >/dev/null 2>&1 || exit 0

copy_dataset() {
  rm -rf "$DATASET_DIR"
  mkdir -p "$DATASET_DIR/campaigns" "$RECORDER_DST"
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

  # High-frequency trades/L2/features stay local. Git gets only compact,
  # campaign-linked summaries that are useful for later indexing/analysis.
  if [[ -d "$RECORDER_SRC" ]]; then
    while IFS= read -r source_file; do
      relative="${source_file#"$RECORDER_SRC"/}"
      destination="$RECORDER_DST/$relative"
      mkdir -p "$(dirname "$destination")"
      cp -f "$source_file" "$destination"
    done < <(
      find "$RECORDER_SRC" -type f \
        \( -name 'metadata.json' -o -name 'events.jsonl' -o -name 'footprint.json' -o -name 'dataset_manifest.json' \) \
        -print
    )
  fi
}

for attempt in 1 2 3; do
  # Always rebuild on top of the newest remote journal head. If another sync
  # wins the push race, fetch that head and retry with the same local snapshot.
  git -C "$SYNC_ROOT" fetch -q origin "$REMOTE_BRANCH" || exit 0
  git -C "$SYNC_ROOT" checkout -q -B galka-research-sync "origin/$REMOTE_BRANCH" || exit 0

  copy_dataset

  # Refuse to publish obvious secret-shaped files even if somebody accidentally
  # places one under the local research directory later.
  if find "$DATASET_DIR" -type f \( -iname '*key*' -o -iname '*secret*' -o -iname '*token*' -o -iname '*.env' \) | grep -q .; then
    exit 0
  fi

  # Dataset files contain only research telemetry. The live config/state/runtime
  # directories and high-frequency raw recorder files are never copied here.
  git -C "$SYNC_ROOT" add datasets/live
  if git -C "$SYNC_ROOT" diff --cached --quiet; then
    exit 0
  fi

  git -C "$SYNC_ROOT" -c user.name='Galka Research Sync' -c user.email='galka-research@local' \
    commit -q -m "Sync Galka research dataset $(date -u +%Y-%m-%dT%H:%M:%SZ)" || exit 0

  if git -C "$SYNC_ROOT" push -q origin HEAD:"$REMOTE_BRANCH"; then
    exit 0
  fi

  sleep "$attempt"
done

exit 0

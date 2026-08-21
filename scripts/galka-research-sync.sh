#!/data/data/com.termux/files/usr/bin/bash
# shellcheck disable=SC1091
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/galka-live-common.sh"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RESEARCH_ROOT="$GALKA_LIVE_DATA_DIR/research"
CAMPAIGN_SRC="$RESEARCH_ROOT/campaigns"
RECORDER_SRC="$RESEARCH_ROOT/galka_campaigns"
CLUSTER_SRC="$RESEARCH_ROOT/clusters/archive"
CLUSTER_MERGER="$REPO_ROOT/scripts/merge-cluster-archive.py"
SYNC_ROOT="${XDG_CACHE_HOME:-$HOME/.cache}/galka-research-sync"
REMOTE_BRANCH="data/galka-live-journal"
REMOTE_URL="https://github.com/jonicbecks-lab/Botizvideo.git"
DATASET_DIR="$SYNC_ROOT/datasets/live"
RECORDER_DST="$DATASET_DIR/recorder"
CLUSTER_DST="$DATASET_DIR/clusters"
LOCK_DIR="${SYNC_ROOT}.lock"
LOCK_PID="$LOCK_DIR/pid"

[[ "${1:-}" == "--once" ]] || {
  echo "Usage: bash scripts/galka-research-sync.sh --once"
  exit 2
}

# Research sync is deliberately best-effort and must never affect trading.
[[ -d "$RESEARCH_ROOT" ]] || exit 0
mkdir -p "$(dirname "$SYNC_ROOT")"

lock_owner_alive() {
  [[ -f "$LOCK_PID" ]] || return 1
  local owner
  owner="$(tr -cd '0-9' < "$LOCK_PID" 2>/dev/null || true)"
  [[ -n "$owner" ]] && kill -0 "$owner" 2>/dev/null
}

acquire_lock() {
  if mkdir "$LOCK_DIR" 2>/dev/null; then
    printf '%s\n' "$$" > "$LOCK_PID"
    return 0
  fi

  # A previous Termux/server process can be killed while holding the old
  # directory lock. Never let that stale directory disable research sync for
  # the rest of the day. If a real owner is still alive, leave it alone.
  if lock_owner_alive; then
    return 1
  fi

  local stale="${LOCK_DIR}.stale.$$"
  if ! mv "$LOCK_DIR" "$stale" 2>/dev/null; then
    return 1
  fi
  rm -rf "$stale" 2>/dev/null || true

  if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    return 1
  fi
  printf '%s\n' "$$" > "$LOCK_PID"
  return 0
}

release_lock() {
  local owner=""
  if [[ -f "$LOCK_PID" ]]; then
    owner="$(tr -cd '0-9' < "$LOCK_PID" 2>/dev/null || true)"
  fi
  if [[ "$owner" == "$$" ]]; then
    rm -rf "$LOCK_DIR" 2>/dev/null || true
  fi
}

if ! acquire_lock; then
  exit 0
fi
trap release_lock EXIT INT TERM

if [[ ! -d "$SYNC_ROOT/.git" ]]; then
  rm -rf "$SYNC_ROOT"
  # Use a canonical URL rather than copying origin from the trading checkout:
  # an origin URL can legally contain credentials and must never be persisted
  # into the research cache clone.
  git clone --no-checkout "$REMOTE_URL" "$SYNC_ROOT" >/dev/null 2>&1 || exit 0
fi

git -C "$SYNC_ROOT" remote set-url origin "$REMOTE_URL" >/dev/null 2>&1 || exit 0

copy_dataset() {
  # Keep the checked-out cluster directory: it is the remote side of the merge.
  # Other generated research views are rebuilt as before.
  mkdir -p "$DATASET_DIR"
  rm -rf "$DATASET_DIR/campaigns" "$RECORDER_DST"
  rm -f "$DATASET_DIR/manifest.json" "$DATASET_DIR/events.jsonl" "$DATASET_DIR/fills.jsonl"
  mkdir -p "$DATASET_DIR/campaigns" "$RECORDER_DST" "$CLUSTER_DST" "$CLUSTER_SRC"

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

  # Cluster cells are compact minute x price buckets, not raw trades. Merge the
  # phone snapshot into the checked-out remote snapshot by deterministic key.
  # Local files stay read-only during sync so the live websocket cannot lose an
  # append if a GitHub sync happens at the same moment.
  if [[ -f "$CLUSTER_MERGER" ]]; then
    python3 "$CLUSTER_MERGER" "$CLUSTER_SRC" "$CLUSTER_DST" >/dev/null 2>&1 || true
  fi
}

for attempt in 1 2 3; do
  # Always rebuild on top of the newest remote journal head. The sync checkout
  # is disposable cache, so clean leftovers from an interrupted previous run
  # before copying the new local snapshot.
  git -C "$SYNC_ROOT" fetch -q origin "$REMOTE_BRANCH" || exit 0
  git -C "$SYNC_ROOT" checkout -q -B galka-research-sync "origin/$REMOTE_BRANCH" || exit 0
  git -C "$SYNC_ROOT" reset -q --hard "origin/$REMOTE_BRANCH" || exit 0
  git -C "$SYNC_ROOT" clean -q -fd -- datasets/live >/dev/null 2>&1 || true

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

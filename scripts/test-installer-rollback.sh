#!/usr/bin/env bash
set -euo pipefail

SOURCE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/galka-installer-test.XXXXXX")"
cleanup() { rm -rf "$TEST_ROOT"; }
trap cleanup EXIT

SEED="$TEST_ROOT/seed"
REMOTE="$TEST_ROOT/remote.git"
HOME_DIR="$TEST_ROOT/home"
PRODUCTION="$HOME_DIR/GalkaLive"
BACKUPS="$HOME_DIR/GalkaLive-backups"
BRANCH="agent/galka-live-hardening-v3"
mkdir -p "$SEED/scripts" "$HOME_DIR/.config" "$HOME_DIR/.local/share/galka-live"
cp "$SOURCE_ROOT/scripts/apply-galka-hardening-v3.sh" "$SEED/scripts/"
cp "$SOURCE_ROOT/scripts/install-galka-live.sh" "$SEED/scripts/"
cp "$SOURCE_ROOT/scripts/rollback-galka-live.sh" "$SEED/scripts/"
cp "$SOURCE_ROOT/scripts/check-repository-secrets.py" "$SEED/scripts/"
cat > "$SEED/scripts/setup-galka-live.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
exit 0
EOF
cat > "$SEED/scripts/verify-galka-live.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
test -f version.txt
EOF
chmod +x "$SEED/scripts/"*
printf 'v1\n' > "$SEED/version.txt"
git -C "$SEED" init -q
git -C "$SEED" config user.name 'Installer Test'
git -C "$SEED" config user.email 'installer@example.invalid'
git -C "$SEED" checkout -qb "$BRANCH"
git -C "$SEED" add .
git -C "$SEED" commit -qm v1
V1="$(git -C "$SEED" rev-parse HEAD)"
git clone -q --bare "$SEED" "$REMOTE"
git -C "$SEED" remote add origin "$REMOTE"

HOME="$HOME_DIR" GALKA_INSTALL_DIR="$PRODUCTION" GALKA_INSTALL_REMOTE="$REMOTE" \
  GALKA_ALLOW_LOCAL_REMOTE=1 GALKA_SOURCE_ONLY=1 \
  bash "$SOURCE_ROOT/scripts/install-galka-live.sh"
[[ "$(git -C "$PRODUCTION" rev-parse HEAD)" == "$V1" ]]

printf 'HL_LIVE_ENABLED=NO\n' > "$HOME_DIR/.config/galka-live.env"
chmod 600 "$HOME_DIR/.config/galka-live.env"
printf 'state-v1\n' > "$HOME_DIR/.local/share/galka-live/state.json"
printf 'v2\n' > "$SEED/version.txt"
git -C "$SEED" add version.txt
git -C "$SEED" commit -qm v2
git -C "$SEED" push -q origin "$BRANCH"
V2="$(git -C "$SEED" rev-parse HEAD)"

if HOME="$HOME_DIR" GALKA_REPO_DIR="$PRODUCTION" GALKA_BACKUP_ROOT="$PRODUCTION/backups" \
  GALKA_ALLOW_LOCAL_REMOTE=1 GALKA_SKIP_DEPENDENCIES=1 \
  bash "$PRODUCTION/scripts/apply-galka-hardening-v3.sh" >/dev/null 2>&1; then
  echo "repo-local backup root unexpectedly succeeded" >&2
  exit 1
fi

HOME="$HOME_DIR" GALKA_REPO_DIR="$PRODUCTION" GALKA_BACKUP_ROOT="$BACKUPS" \
  GALKA_ALLOW_LOCAL_REMOTE=1 GALKA_SKIP_DEPENDENCIES=1 \
  bash "$PRODUCTION/scripts/apply-galka-hardening-v3.sh"
[[ "$(git -C "$PRODUCTION" rev-parse HEAD)" == "$V2" ]]
[[ -z "$(git -C "$PRODUCTION" stash list)" ]]
BACKUP="$(find "$BACKUPS" -mindepth 1 -maxdepth 1 -type d ! -name 'pre-rollback-*' | sort | tail -n 1)"
[[ -n "$BACKUP" && "$(<"$BACKUP/previous-commit.txt")" == "$V1" ]]
(cd "$BACKUP" && sha256sum --check --quiet checksums.sha256)

printf 'dirty\n' >> "$PRODUCTION/version.txt"
if HOME="$HOME_DIR" GALKA_REPO_DIR="$PRODUCTION" GALKA_BACKUP_ROOT="$BACKUPS" \
  GALKA_ALLOW_LOCAL_REMOTE=1 GALKA_SKIP_DEPENDENCIES=1 \
  bash "$PRODUCTION/scripts/apply-galka-hardening-v3.sh" >/dev/null 2>&1; then
  echo "dirty updater unexpectedly succeeded" >&2
  exit 1
fi
git -C "$PRODUCTION" restore version.txt
printf 'state-v2\n' > "$HOME_DIR/.local/share/galka-live/state.json"
printf 'HL_LIVE_ENABLED=YES\n' > "$HOME_DIR/.config/galka-live.env"
if HOME="$HOME_DIR" GALKA_REPO_DIR="$PRODUCTION" GALKA_BACKUP_ROOT="$BACKUPS" \
  GALKA_ALLOW_LOCAL_REMOTE=1 GALKA_SKIP_DEPENDENCIES=1 \
  bash "$PRODUCTION/scripts/rollback-galka-live.sh" "$BACKUP" >/dev/null 2>&1; then
  echo "LIVE-enabled rollback unexpectedly succeeded" >&2
  exit 1
fi
printf 'HL_LIVE_ENABLED=NO\n' > "$HOME_DIR/.config/galka-live.env"

HOME="$HOME_DIR" GALKA_REPO_DIR="$PRODUCTION" GALKA_BACKUP_ROOT="$BACKUPS" \
  GALKA_ALLOW_LOCAL_REMOTE=1 GALKA_SKIP_DEPENDENCIES=1 \
  bash "$PRODUCTION/scripts/rollback-galka-live.sh" "$BACKUP"
[[ "$(git -C "$PRODUCTION" rev-parse HEAD)" == "$V1" ]]
[[ "$(<"$HOME_DIR/.local/share/galka-live/state.json")" == "state-v1" ]]
[[ "$(<"$HOME_DIR/.config/galka-live.env")" == "HL_LIVE_ENABLED=NO" ]]
[[ "$(git -C "$PRODUCTION" branch --show-current)" == rollback/galka-live-* ]]

echo "Installer/rollback: clean fast-forward, dirty-tree refusal and recoverable rollback passed"

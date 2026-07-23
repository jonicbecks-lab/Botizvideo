#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMPORT_SCRIPT="$SOURCE_ROOT/scripts/import-production-bundle.sh"
BOOTSTRAP_SCRIPT="$SOURCE_ROOT/scripts/termux-sync-and-prepare-galka.sh"
BASE_COMMIT="d5a52564b047ab9e00f154d3816dab053fad417a"
BRANCH="agent/galka-live-hardening-v3"
OLD_REPO_URL="https://github.com/CryptoJonic/MeteoraAgent.git"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/galka-bundle-import-test.XXXXXX")"

cleanup() {
  rm -rf "$TEST_ROOT"
}
trap cleanup EXIT

fail() {
  printf 'BUNDLE IMPORT TEST FAILED: %s\n' "$*" >&2
  exit 1
}

configure_identity() {
  git -C "$1" config user.name "Bundle Import Test"
  git -C "$1" config user.email "bundle-import@example.invalid"
}

write_runtime_stubs() {
  local root="$1"
  cat > "$root/scripts/setup-galka-live.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
[[ "${1:-}" == "--dependencies-only" ]]
printf 'fixture installer pass\n'
EOF
  cat > "$root/scripts/verify-galka-live.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf 'fixture preflight pass\n'
EOF
  cat > "$root/scripts/check-galka-live-account.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
config="${GALKA_LIVE_CONFIG:-${XDG_CONFIG_HOME:-$HOME/.config}/galka-live.env}"
grep -Fxq 'HL_LIVE_ENABLED=NO' "$config"
printf 'READ-ONLY CHECK PASS: торговые команды не отправлялись.\n'
EOF
  chmod +x "$root/scripts/setup-galka-live.sh" \
    "$root/scripts/verify-galka-live.sh" \
    "$root/scripts/check-galka-live-account.sh"
}

make_mock_gh() {
  local bin_dir="$1"
  mkdir -p "$bin_dir"
  cat > "$bin_dir/gh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
[[ "${1:-}" == "--version" ]]
printf 'gh version fixture\n'
EOF
  chmod +x "$bin_dir/gh"
}

prepare_case() {
  local name="$1"
  local case_root="$TEST_ROOT/$name"
  local producer="$case_root/producer"
  local home_dir="$case_root/home"
  local repo="$home_dir/GalkaLive"
  local bundle="$case_root/production.bundle"

  mkdir -p "$case_root" "$home_dir/.config"
  git clone -q --no-hardlinks "$SOURCE_ROOT" "$producer"
  configure_identity "$producer"
  git -C "$producer" checkout -qB "$BRANCH" "$BASE_COMMIT"
  git clone -q --no-hardlinks "$producer" "$repo"
  configure_identity "$repo"

  cp "$BOOTSTRAP_SCRIPT" "$producer/scripts/termux-sync-and-prepare-galka.sh"
  cp "$IMPORT_SCRIPT" "$producer/scripts/import-production-bundle.sh"
  cp "$SOURCE_ROOT/scripts/test-termux-sync-and-prepare-galka.sh" \
    "$producer/scripts/test-termux-sync-and-prepare-galka.sh"
  cp "$SOURCE_ROOT/scripts/test-import-production-bundle.sh" \
    "$producer/scripts/test-import-production-bundle.sh"
  cp "$SOURCE_ROOT/PRODUCTION_READINESS_REPORT.md" \
    "$producer/PRODUCTION_READINESS_REPORT.md"
  write_runtime_stubs "$producer"
  printf 'bundle-production\n' > "$producer/bundle-version.txt"
  git -C "$producer" add .
  git -C "$producer" commit -qm "fixture production bundle"
  git -C "$producer" bundle create "$bundle" "refs/heads/$BRANCH"
  git -C "$producer" bundle verify "$bundle" >/dev/null

  printf 'HL_LIVE_ENABLED=NO\n' > "$home_dir/.config/galka-live.env"
  chmod 600 "$home_dir/.config/galka-live.env"
  git -C "$repo" remote set-url origin "$OLD_REPO_URL"
  git -C "$repo" config --unset-all remote.origin.fetch || true
  git -C "$repo" config --add remote.origin.fetch \
    '+refs/heads/feat/galka-terminal-mvp:refs/remotes/origin/feat/galka-terminal-mvp'
  make_mock_gh "$case_root/bin"

  printf '%s\t%s\t%s\t%s\t%s\n' \
    "$case_root" "$home_dir" "$repo" "$producer" "$bundle"
}

run_import() {
  local case_root="$1"
  local home_dir="$2"
  local bundle="$3"
  local output="$4"
  HOME="$home_dir" PATH="$case_root/bin:$PATH" \
    bash "$IMPORT_SCRIPT" "$bundle" > "$output" 2>&1
}

IFS=$'\t' read -r case_root home_dir repo producer bundle < <(prepare_case success)
config_hash_before="$(sha256sum "$home_dir/.config/galka-live.env" | awk '{print $1}')"
run_import "$case_root" "$home_dir" "$bundle" "$case_root/output.log"
expected_head="$(git -C "$producer" rev-parse HEAD)"
[[ "$(git -C "$repo" rev-parse HEAD)" == "$expected_head" ]] || \
  fail "import не достиг production HEAD"
[[ -z "$(git -C "$repo" status --porcelain=v1 --untracked-files=all)" ]] || \
  fail "import оставил dirty worktree"
[[ "$(git -C "$repo" config --get remote.origin.url)" == \
   "https://github.com/jonicbecks-lab/Botizvideo.git" ]] || \
  fail "bootstrap не исправил origin"
[[ "$(git -C "$repo" config --get remote.legacy-origin.url)" == "$OLD_REPO_URL" ]] || \
  fail "bootstrap не сохранил legacy-origin"
[[ "$(git -C "$repo" config --get-all remote.origin.fetch)" == \
   '+refs/heads/*:refs/remotes/origin/*' ]] || \
  fail "bootstrap не исправил fetch refspec"
config_hash_after="$(sha256sum "$home_dir/.config/galka-live.env" | awk '{print $1}')"
[[ "$config_hash_before" == "$config_hash_after" ]] || fail "LIVE config изменён"
for expected in \
  "SYNC PASS" "INSTALL PASS" "PREFLIGHT PASS" "READ ONLY PASS" \
  "LIVE OFF" "ORDERS SENT: NO" "READY" "BUNDLE IMPORT PASS" \
  "PRODUCTION HEAD: $expected_head"; do
  grep -Fxq "$expected" "$case_root/output.log" || fail "нет итоговой строки: $expected"
done

IFS=$'\t' read -r case_root home_dir repo producer bundle < <(prepare_case dirty)
printf 'dirty\n' >> "$repo/README.md"
if run_import "$case_root" "$home_dir" "$bundle" "$case_root/output.log"; then
  fail "dirty worktree неожиданно импортирован"
fi
grep -Fq 'рабочее дерево содержит изменения' "$case_root/output.log" || \
  fail "dirty refusal не сообщил точную причину"

IFS=$'\t' read -r case_root home_dir repo producer bundle < <(prepare_case invalid_bundle)
printf 'not a git bundle\n' > "$case_root/invalid.bundle"
if run_import "$case_root" "$home_dir" "$case_root/invalid.bundle" \
  "$case_root/output.log"; then
  fail "повреждённый bundle неожиданно импортирован"
fi
grep -Fq 'does not look like a v2 or v3 bundle file' "$case_root/output.log" || \
  fail "повреждённый bundle не был отклонён проверкой Git"
[[ "$(git -C "$repo" rev-parse HEAD)" == "$BASE_COMMIT" ]] || \
  fail "HEAD изменён при отказе повреждённого bundle"

IFS=$'\t' read -r case_root home_dir repo producer bundle < <(prepare_case diverged)
printf 'local divergence\n' > "$repo/local-divergence.txt"
git -C "$repo" add local-divergence.txt
git -C "$repo" commit -qm "local divergence"
local_head="$(git -C "$repo" rev-parse HEAD)"
if run_import "$case_root" "$home_dir" "$bundle" "$case_root/output.log"; then
  fail "diverged history неожиданно импортирована"
fi
grep -Fq 'не является fast-forward' "$case_root/output.log" || \
  fail "divergence не сообщила точную причину"
[[ "$(git -C "$repo" rev-parse HEAD)" == "$local_head" ]] || \
  fail "diverged HEAD был изменён"

if grep -Eq \
  'git[[:space:]]+(reset|clean|rebase|checkout)|--force([[:space:]]|$)|force-with-lease' \
  "$IMPORT_SCRIPT"; then
  fail "importer содержит запрещённую Git-команду"
fi
if grep -E 'git .*merge ' "$IMPORT_SCRIPT" | grep -Fv -- 'merge --ff-only' >/dev/null; then
  fail "importer содержит merge без --ff-only"
fi

printf 'Production bundle import: verify, full branch, fast-forward, corrupt/dirty/diverged refusal and LIVE OFF passed\n'

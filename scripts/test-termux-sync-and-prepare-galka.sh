#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$SOURCE_ROOT/scripts/termux-sync-and-prepare-galka.sh"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/galka-termux-sync-test.XXXXXX")"
REPO_URL="https://github.com/jonicbecks-lab/Botizvideo.git"
OLD_REPO_URL="https://github.com/CryptoJonic/MeteoraAgent.git"
BRANCH="agent/galka-live-hardening-v3"

cleanup() {
  rm -rf "$TEST_ROOT"
}
trap cleanup EXIT

fail() {
  printf 'TERMUX SYNC TEST FAILED: %s\n' "$*" >&2
  exit 1
}

configure_identity() {
  git -C "$1" config user.name "Termux Sync Test"
  git -C "$1" config user.email "termux-sync@example.invalid"
}

write_runtime_stubs() {
  local root="$1"
  mkdir -p "$root/scripts"
  cp "$SCRIPT" "$root/scripts/"
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
grep -Eq '^[[:space:]]*HL_LIVE_ENABLED[[:space:]]*=[[:space:]]*NO[[:space:]]*$' "$config"
printf 'READ-ONLY CHECK PASS: торговые команды не отправлялись.\n'
EOF
  chmod +x "$root/scripts/"*.sh
}

make_mock_gh() {
  local bin_dir="$1"
  mkdir -p "$bin_dir"
  cat > "$bin_dir/gh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
case "$*" in
  "--version") printf 'gh version fixture\n'; exit 0 ;;
  "auth status --active --hostname github.com"|"auth setup-git --hostname github.com") exit 0 ;;
  *) printf 'unexpected gh invocation: %s\n' "$*" >&2; exit 2 ;;
esac
EOF
  chmod +x "$bin_dir/gh"
}

prepare_case() {
  local name="$1"
  local mode="${2:-clone}"
  local case_root="$TEST_ROOT/$name"
  local seed="$case_root/seed"
  local remote="$case_root/remote.git"
  local home_dir="$case_root/home"
  local repo="$home_dir/GalkaLive"
  local main_clone="$case_root/main-clone"

  mkdir -p "$seed" "$home_dir/.config"
  git -C "$seed" init -q
  configure_identity "$seed"
  git -C "$seed" checkout -qb "$BRANCH"
  write_runtime_stubs "$seed"
  printf 'v1\n' > "$seed/version.txt"
  git -C "$seed" add .
  git -C "$seed" commit -qm "fixture v1"
  git clone -q --bare "$seed" "$remote"
  git -C "$seed" remote add origin "$remote"

  if [[ "$mode" == "worktree" ]]; then
    git clone -q "$remote" "$main_clone"
    configure_identity "$main_clone"
    git -C "$main_clone" switch -q --detach
    git -C "$main_clone" worktree add -q "$repo" "$BRANCH"
  else
    git clone -q "$remote" "$repo"
    configure_identity "$repo"
  fi

  printf 'v2\n' > "$seed/version.txt"
  git -C "$seed" add version.txt
  git -C "$seed" commit -qm "fixture v2"
  git -C "$seed" push -q origin "$BRANCH"

  printf 'HL_LIVE_ENABLED=NO\nHL_LIVE_CONFIRM=NOT_CONFIRMED\n' \
    > "$home_dir/.config/galka-live.env"
  chmod 600 "$home_dir/.config/galka-live.env"
  git -C "$repo" remote set-url origin "$OLD_REPO_URL"
  git -C "$repo" config --unset-all remote.origin.fetch || true
  git -C "$repo" config --add remote.origin.fetch \
    '+refs/heads/feat/galka-terminal-mvp:refs/remotes/origin/feat/galka-terminal-mvp'
  git -C "$repo" config --add remote.origin.fetch \
    '+refs/heads/agent/galka-live-hardening-v3:refs/remotes/origin/agent/galka-live-hardening-v3'

  git config --file "$home_dir/.gitconfig" \
    "url.file://$remote.insteadOf" "$REPO_URL"
  make_mock_gh "$case_root/bin"

  printf '%s\t%s\t%s\t%s\n' "$case_root" "$home_dir" "$repo" "$seed"
}

run_bootstrap() {
  local case_root="$1"
  local home_dir="$2"
  local output_file="$3"
  shift 3
  HOME="$home_dir" \
  GALKA_LIVE_CONFIG="$home_dir/.config/galka-live.env" \
  PATH="$case_root/bin:$PATH" \
  bash "$SCRIPT" "$@" > "$output_file" 2>&1
}

assert_final_output() {
  local output_file="$1"
  for expected in \
    "SYNC PASS" \
    "INSTALL PASS" \
    "PREFLIGHT PASS" \
    "READ ONLY PASS" \
    "LIVE OFF" \
    "ORDERS SENT: NO" \
    "READY"; do
    grep -Fxq "$expected" "$output_file" || \
      fail "нет итоговой строки: $expected"
  done
}

IFS=$'\t' read -r case_root home_dir repo seed < <(prepare_case fast_forward worktree)
[[ -f "$repo/.git" ]] || fail "worktree fixture не создал .git-файл"
config_hash_before="$(sha256sum "$home_dir/.config/galka-live.env" | awk '{print $1}')"
run_bootstrap "$case_root" "$home_dir" "$case_root/output.log"
[[ "$(git -C "$repo" rev-parse HEAD)" == "$(git -C "$seed" rev-parse HEAD)" ]] || \
  fail "fast-forward не достиг remote HEAD"
[[ "$(git -C "$repo" config --get remote.origin.url)" == "$REPO_URL" ]] || \
  fail "origin не исправлен"
[[ "$(git -C "$repo" config --get remote.legacy-origin.url)" == "$OLD_REPO_URL" ]] || \
  fail "старый origin не сохранён как legacy-origin"
mapfile -t fetch_specs < <(git -C "$repo" config --get-all remote.origin.fetch)
[[ "${#fetch_specs[@]}" -eq 1 ]] || fail "origin содержит лишние fetch refspec"
[[ "${fetch_specs[0]}" == '+refs/heads/*:refs/remotes/origin/*' ]] || \
  fail "origin fetch refspec не стандартный"
config_hash_after="$(sha256sum "$home_dir/.config/galka-live.env" | awk '{print $1}')"
[[ "$config_hash_before" == "$config_hash_after" ]] || fail "LIVE config был изменён"
grep -Fxq 'HL_LIVE_ENABLED=NO' "$home_dir/.config/galka-live.env" || \
  fail "LIVE перестал быть OFF"
assert_final_output "$case_root/output.log"

IFS=$'\t' read -r case_root home_dir repo seed < <(prepare_case dirty)
printf 'dirty\n' >> "$repo/version.txt"
if run_bootstrap "$case_root" "$home_dir" "$case_root/output.log"; then
  fail "dirty worktree неожиданно синхронизирован"
fi
grep -Fq 'рабочее дерево содержит изменения' "$case_root/output.log" || \
  fail "dirty worktree не сообщил точную причину"
[[ "$(git -C "$repo" config --get remote.origin.url)" == "$OLD_REPO_URL" ]] || \
  fail "origin изменён до dirty-tree refusal"

IFS=$'\t' read -r case_root home_dir repo seed < <(prepare_case wrong_branch)
git -C "$repo" branch -m not-production
if run_bootstrap "$case_root" "$home_dir" "$case_root/output.log"; then
  fail "неверная ветка неожиданно синхронизирована"
fi
grep -Fq "ожидалась ветка $BRANCH" "$case_root/output.log" || \
  fail "неверная ветка не сообщила точную причину"
[[ "$(git -C "$repo" config --get remote.origin.url)" == "$OLD_REPO_URL" ]] || \
  fail "origin изменён до branch refusal"

IFS=$'\t' read -r case_root home_dir repo seed < <(prepare_case live_on)
printf 'HL_LIVE_ENABLED=YES\nHL_LIVE_CONFIRM=I_UNDERSTAND_REAL_MONEY\n' \
  > "$home_dir/.config/galka-live.env"
if run_bootstrap "$case_root" "$home_dir" "$case_root/output.log"; then
  fail "bootstrap неожиданно продолжился при LIVE ON"
fi
grep -Fq 'LIVE должен быть явно выключен' "$case_root/output.log" || \
  fail "LIVE ON не сообщил точную причину"
[[ "$(git -C "$repo" config --get remote.origin.url)" == "$OLD_REPO_URL" ]] || \
  fail "origin изменён до LIVE-off refusal"

IFS=$'\t' read -r case_root home_dir repo seed < <(prepare_case diverged)
printf 'local-only\n' > "$repo/local-only.txt"
git -C "$repo" add local-only.txt
git -C "$repo" commit -qm "local divergence"
if run_bootstrap "$case_root" "$home_dir" "$case_root/output.log"; then
  fail "diverged history неожиданно синхронизирована"
fi
grep -Fq 'истории разошлись' "$case_root/output.log" || \
  fail "diverged history не сообщила точную причину"
[[ -f "$repo/local-only.txt" ]] || fail "diverged local commit был потерян"

IFS=$'\t' read -r case_root home_dir repo seed < <(prepare_case no_gh)
minimal_path="$case_root/no-gh-bin"
mkdir -p "$minimal_path"
for command_name in bash git python; do
  ln -s "$(command -v "$command_name")" "$minimal_path/$command_name"
done
if HOME="$home_dir" GALKA_LIVE_CONFIG="$home_dir/.config/galka-live.env" PATH="$minimal_path" /usr/bin/bash "$SCRIPT" \
  > "$case_root/output.log" 2>&1; then
  fail "bootstrap без gh неожиданно продолжился"
fi
grep -Fq 'не найдена обязательная команда: gh' "$case_root/output.log" || \
  fail "отсутствие gh не диагностировано"

if grep -Eq \
  'git[[:space:]]+(reset|clean|rebase|checkout)|--force([[:space:]]|$)|force-with-lease' \
  "$SCRIPT"; then
  fail "bootstrap содержит запрещённую Git-команду"
fi
if grep -E 'git .*merge ' "$SCRIPT" | grep -Fv -- 'merge --ff-only' >/dev/null; then
  fail "bootstrap содержит merge без --ff-only"
fi

printf 'Termux sync bootstrap: origin repair, refspec cleanup, worktree, branch, dirty, fast-forward, bundle prepare, gh and LIVE safety passed\n'

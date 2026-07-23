#!/usr/bin/env bash
set -Eeuo pipefail

REPO_DIR="$HOME/GalkaLive"
REPO_URL="https://github.com/jonicbecks-lab/Botizvideo.git"
BRANCH="agent/galka-live-hardening-v3"
REMOTE_REF="refs/remotes/origin/$BRANCH"
BASE_COMMIT="d5a52564b047ab9e00f154d3816dab053fad417a"
CONFIG_FILE="${GALKA_LIVE_CONFIG:-${XDG_CONFIG_HOME:-$HOME/.config}/galka-live.env}"
MODE="sync"
EXPECTED_LOCAL_HEAD=""

case "${1:-}" in
  "")
    ;;
  --prepare-local)
    MODE="prepare-local"
    EXPECTED_LOCAL_HEAD="${2:-}"
    [[ -n "$EXPECTED_LOCAL_HEAD" && -z "${3:-}" ]] || {
      printf 'Usage: bash scripts/termux-sync-and-prepare-galka.sh [--prepare-local <expected-head>]\n' >&2
      exit 2
    }
    ;;
  *)
    printf 'Usage: bash scripts/termux-sync-and-prepare-galka.sh [--prepare-local <expected-head>]\n' >&2
    exit 2
    ;;
esac

die() {
  printf 'SYNC STOPPED: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "не найдена обязательная команда: $1"
}

stored_remote_url() {
  git -C "$REPO_DIR" config --get "remote.$1.url" 2>/dev/null || true
}

live_is_off() {
  [[ -f "$CONFIG_FILE" && ! -L "$CONFIG_FILE" ]] || return 1
  python - "$CONFIG_FILE" <<'PY'
from pathlib import Path
import sys

values = []
for raw_line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    line = raw_line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    if key.strip() == "HL_LIVE_ENABLED":
        values.append(value.strip().upper())
raise SystemExit(0 if values == ["NO"] else 1)
PY
}

for required in bash git gh python; do
  require_command "$required"
done
gh --version >/dev/null 2>&1 || die "GitHub CLI gh не запускается"
[[ "$CONFIG_FILE" == /* ]] || die "путь LIVE config должен быть абсолютным: $CONFIG_FILE"

[[ "$REPO_DIR" != "/" && "$REPO_DIR" != "$HOME" ]] || \
  die "небезопасный путь репозитория: $REPO_DIR"
[[ -d "$REPO_DIR" ]] || die "не найден каталог репозитория: $REPO_DIR"
git -C "$REPO_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1 || \
  die "$REPO_DIR не является Git worktree"
if python - "$REPO_DIR" "$CONFIG_FILE" <<'PY'
from pathlib import Path
import sys

try:
    Path(sys.argv[2]).resolve(strict=False).relative_to(
        Path(sys.argv[1]).resolve(strict=True)
    )
except ValueError:
    raise SystemExit(1)
PY
then
  die "LIVE config должен находиться вне Git-репозитория"
fi
export GALKA_LIVE_CONFIG="$CONFIG_FILE"

CURRENT_BRANCH="$(git -C "$REPO_DIR" branch --show-current)"
[[ "$CURRENT_BRANCH" == "$BRANCH" ]] || \
  die "ожидалась ветка $BRANCH, текущая: ${CURRENT_BRANCH:-detached HEAD}"
[[ -z "$(git -C "$REPO_DIR" status --porcelain=v1 --untracked-files=all)" ]] || \
  die "рабочее дерево содержит изменения; синхронизация отменена"

if [[ -e "$CONFIG_FILE" ]]; then
  [[ ! -L "$CONFIG_FILE" ]] || die "LIVE config не должен быть symlink"
  live_is_off || die "LIVE должен быть явно выключен: HL_LIVE_ENABLED=NO"
fi
if command -v pgrep >/dev/null 2>&1 && \
   pgrep -f 'python[^ ]* .*\-m live\.server' >/dev/null 2>&1; then
  die "обнаружен запущенный Galka LIVE; сначала останови его через Ctrl+C"
fi

if [[ "$MODE" == "sync" ]]; then
  gh auth status --active --hostname github.com >/dev/null 2>&1 || \
    die "GitHub CLI не авторизован для github.com"
  gh auth setup-git --hostname github.com >/dev/null
fi

ORIGIN_URL="$(stored_remote_url origin)"
case "$ORIGIN_URL" in
  https://github.com/CryptoJonic/MeteoraAgent|\
  https://github.com/CryptoJonic/MeteoraAgent.git|\
  git@github.com:CryptoJonic/MeteoraAgent.git)
    if [[ -z "$(stored_remote_url legacy-origin)" ]]; then
      git -C "$REPO_DIR" remote rename origin legacy-origin
      git -C "$REPO_DIR" remote add origin "$REPO_URL"
    else
      git -C "$REPO_DIR" remote set-url origin "$REPO_URL"
    fi
    ;;
  https://github.com/jonicbecks-lab/Botizvideo|\
  https://github.com/jonicbecks-lab/Botizvideo.git|\
  git@github.com:jonicbecks-lab/Botizvideo.git)
    git -C "$REPO_DIR" remote set-url origin "$REPO_URL"
    ;;
  "")
    git -C "$REPO_DIR" remote add origin "$REPO_URL"
    ;;
  *)
    die "неожиданный origin оставлен без изменений: $ORIGIN_URL"
    ;;
esac

git -C "$REPO_DIR" config --unset-all remote.origin.fetch || true
git -C "$REPO_DIR" config --add remote.origin.fetch \
  '+refs/heads/*:refs/remotes/origin/*'

if [[ "$MODE" == "sync" ]]; then
  printf '[1/4] Загружаю только %s...\n' "$BRANCH"
  git -C "$REPO_DIR" fetch origin \
    "refs/heads/$BRANCH:refs/remotes/origin/$BRANCH"

  LOCAL_BEFORE="$(git -C "$REPO_DIR" rev-parse HEAD)"
  REMOTE_HEAD="$(git -C "$REPO_DIR" rev-parse "$REMOTE_REF^{commit}")"
  git -C "$REPO_DIR" merge-base --is-ancestor "$LOCAL_BEFORE" "$REMOTE_HEAD" || \
    die "локальная и удалённая истории разошлись; fast-forward невозможен"

  printf '[2/4] Выполняю только fast-forward...\n'
  git -C "$REPO_DIR" merge --ff-only "$REMOTE_REF"
  LOCAL_AFTER="$(git -C "$REPO_DIR" rev-parse HEAD)"
  REMOTE_AFTER="$(git -C "$REPO_DIR" rev-parse "$REMOTE_REF")"
  [[ "$LOCAL_AFTER" == "$REMOTE_AFTER" ]] || \
    die "после fast-forward локальный и удалённый HEAD не совпали"
else
  [[ "$EXPECTED_LOCAL_HEAD" =~ ^[0-9a-f]{40,64}$ ]] || \
    die "некорректный expected HEAD bundle"
  LOCAL_AFTER="$(git -C "$REPO_DIR" rev-parse HEAD)"
  [[ "$LOCAL_AFTER" == "$EXPECTED_LOCAL_HEAD" ]] || \
    die "локальный HEAD не совпадает с импортированным bundle HEAD"
  git -C "$REPO_DIR" merge-base --is-ancestor "$BASE_COMMIT" "$LOCAL_AFTER" || \
    die "production base отсутствует в импортированной истории"
  [[ "$(git -C "$REPO_DIR" rev-list --merges --count "$BASE_COMMIT..$LOCAL_AFTER")" == "0" ]] || \
    die "импортированная production-цепочка содержит merge-коммиты"
  printf '[1/4] Bundle HEAD подтверждён: %s\n' "$LOCAL_AFTER"
  printf '[2/4] Сетевой fetch пропущен: подготовка локально импортированного bundle.\n'
fi

cd "$REPO_DIR"
printf '[3/4] Устанавливаю locked-зависимости и выполняю production preflight...\n'
bash scripts/setup-galka-live.sh --dependencies-only
bash scripts/verify-galka-live.sh
[[ -z "$(git status --porcelain=v1 --untracked-files=all)" ]] || \
  die "installer или preflight оставил изменения в рабочем дереве"

[[ -f "$CONFIG_FILE" ]] || \
  die "не найден локальный LIVE config: $CONFIG_FILE"
live_is_off || die "LIVE должен быть явно выключен: HL_LIVE_ENABLED=NO"

printf '[4/4] Выполняю Hyperliquid read-only проверку...\n'
bash scripts/check-galka-live-account.sh
live_is_off || die "LIVE изменился во время проверки"
if [[ "$MODE" == "sync" ]]; then
  [[ "$(git rev-parse HEAD)" == "$(git rev-parse "$REMOTE_REF")" ]] || \
    die "итоговый локальный HEAD не совпадает с origin/$BRANCH"
else
  [[ "$(git rev-parse HEAD)" == "$EXPECTED_LOCAL_HEAD" ]] || \
    die "HEAD изменился во время локальной подготовки"
fi
[[ -z "$(git status --porcelain=v1 --untracked-files=all)" ]] || \
  die "проверки оставили изменения в рабочем дереве"

printf '\nSYNC PASS\n'
printf 'INSTALL PASS\n'
printf 'PREFLIGHT PASS\n'
printf 'READ ONLY PASS\n'
printf 'LIVE OFF\n'
printf 'ORDERS SENT: NO\n'
printf 'READY\n'

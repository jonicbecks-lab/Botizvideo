#!/data/data/com.termux/files/usr/bin/bash
set -Eeuo pipefail

REPO="${GALKA_REPO_DIR:-$HOME/GalkaLive}"
BRANCH="agent/galka-live-hardening-v3"
REMOTE="origin"
REMOTE_REF="refs/remotes/$REMOTE/$BRANCH"
BACKUP_PARENT="${GALKA_BACKUP_ROOT:-$HOME/GalkaLive-backups}"
CONFIG="${GALKA_LIVE_CONFIG:-${XDG_CONFIG_HOME:-$HOME/.config}/galka-live.env}"
STATE_DIR="${GALKA_STATE_DIR:-$HOME/.local/share/galka-live}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)-$$"
BACKUP_DIR="$BACKUP_PARENT/$STAMP"
BACKUP_READY=0

on_error() {
  local status=$?
  printf '\nОБНОВЛЕНИЕ ОСТАНОВЛЕНО (код %s).\n' "$status" >&2
  if [[ "$BACKUP_READY" -eq 1 ]]; then
    printf 'Код и данные не удалены. Резервная копия: %s\n' "$BACKUP_DIR" >&2
    printf 'Rollback: bash scripts/rollback-galka-live.sh %q\n' "$BACKUP_DIR" >&2
  fi
  exit "$status"
}
trap on_error ERR

die() {
  printf 'ОШИБКА: %s\n' "$*" >&2
  return 1
}

path_is_within() {
  python3 - "$1" "$2" <<'PY'
from pathlib import Path
import sys
try:
    Path(sys.argv[1]).expanduser().resolve(strict=False).relative_to(Path(sys.argv[2]).expanduser().resolve(strict=False))
except ValueError:
    raise SystemExit(1)
PY
}

[[ -d "$REPO" && "$REPO" != "/" && "$REPO" != "$HOME" ]] || \
  die "не найден безопасный путь репозитория: $REPO"
git -C "$REPO" rev-parse --is-inside-work-tree >/dev/null 2>&1 || \
  die "$REPO не является Git-репозиторием"

cd "$REPO"
REPO_REAL="$(pwd -P)"
mkdir -p "$BACKUP_PARENT"
BACKUP_PARENT_REAL="$(cd "$BACKUP_PARENT" && pwd -P)"
path_is_within "$BACKUP_PARENT_REAL" "$REPO_REAL" && \
  die "backup directory должен находиться вне Git-репозитория"
path_is_within "$CONFIG" "$REPO_REAL" && die "LIVE config находится внутри Git-репозитория"
path_is_within "$STATE_DIR" "$REPO_REAL" && die "LIVE state находится внутри Git-репозитория"
BACKUP_DIR="$BACKUP_PARENT_REAL/$STAMP"
CURRENT_BRANCH="$(git branch --show-current)"
[[ "$CURRENT_BRANCH" == "$BRANCH" ]] || \
  die "ожидалась ветка $BRANCH, текущая: ${CURRENT_BRANCH:-detached HEAD}"
[[ -z "$(git status --porcelain=v1 --untracked-files=all)" ]] || \
  die "рабочее дерево содержит изменения; обновление ничего не stash-ит и не коммитит"

REMOTE_URL="$(git remote get-url "$REMOTE")"
if [[ "${GALKA_ALLOW_LOCAL_REMOTE:-0}" != "1" ]]; then
  case "$REMOTE_URL" in
    https://github.com/jonicbecks-lab/Botizvideo|https://github.com/jonicbecks-lab/Botizvideo.git|git@github.com:jonicbecks-lab/Botizvideo.git) ;;
    *) die "неожиданный origin: $REMOTE_URL" ;;
  esac
fi

if command -v pgrep >/dev/null 2>&1 && pgrep -f 'python[^ ]* .*\-m live\.server' >/dev/null 2>&1; then
  die "сначала останови Galka LIVE через Ctrl+C"
fi
if [[ -f "$CONFIG" ]] && grep -Eq '^[[:space:]]*HL_LIVE_ENABLED[[:space:]]*=[[:space:]]*YES[[:space:]]*$' "$CONFIG"; then
  die "обновление разрешено только при HL_LIVE_ENABLED=NO"
fi

python3 scripts/check-repository-secrets.py --history

umask 077
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
git rev-parse HEAD > "$BACKUP_DIR/previous-commit.txt"
printf '%s\n' "$CURRENT_BRANCH" > "$BACKUP_DIR/previous-branch.txt"
printf '%s\n' "$REPO" > "$BACKUP_DIR/repo-path.txt"
printf '%s\n' "$REMOTE_URL" > "$BACKUP_DIR/remote-url.txt"
if [[ -L "$CONFIG" ]]; then
  die "секретный config не должен быть symlink"
elif [[ -f "$CONFIG" ]]; then
  mkdir -p "$BACKUP_DIR/config"
  cp -p "$CONFIG" "$BACKUP_DIR/config/galka-live.env"
  chmod 600 "$BACKUP_DIR/config/galka-live.env"
fi
if [[ -L "$STATE_DIR" ]]; then
  die "state directory не должен быть symlink"
elif [[ -d "$STATE_DIR" ]]; then
  cp -a "$STATE_DIR" "$BACKUP_DIR/state"
fi
: > "$BACKUP_DIR/checksums.sha256"
while IFS= read -r -d '' file; do
  relative="${file#"$BACKUP_DIR/"}"
  (cd "$BACKUP_DIR" && sha256sum "$relative") >> "$BACKUP_DIR/checksums.sha256"
done < <(find "$BACKUP_DIR" -type f ! -name checksums.sha256 -print0 | sort -z)
BACKUP_READY=1

printf '[1/4] Получаю только подготовленную ветку...\n'
git fetch --prune "$REMOTE" "refs/heads/$BRANCH:$REMOTE_REF"
TARGET_COMMIT="$(git rev-parse "$REMOTE_REF^{commit}")"
PREVIOUS_COMMIT="$(<"$BACKUP_DIR/previous-commit.txt")"
git merge-base --is-ancestor "$PREVIOUS_COMMIT" "$TARGET_COMMIT" || \
  die "origin/$BRANCH не является fast-forward; обновление отменено"

printf '[2/4] Выполняю fast-forward без merge commit...\n'
git merge --ff-only "$REMOTE_REF"
[[ "$(git rev-parse HEAD)" == "$TARGET_COMMIT" ]] || die "не достигнут ожидаемый commit"

printf '[3/4] Проверяю зависимости и весь hardened-набор...\n'
python3 scripts/check-repository-secrets.py --history
if [[ "${GALKA_SKIP_DEPENDENCIES:-0}" != "1" ]]; then
  bash scripts/setup-galka-live.sh --dependencies-only
fi
bash scripts/verify-galka-live.sh

printf '[4/4] Проверяю чистоту установки...\n'
[[ -z "$(git status --porcelain=v1 --untracked-files=all)" ]] || \
  die "проверки оставили неожиданные изменения в рабочем дереве"

trap - ERR
printf '\nОБНОВЛЕНИЕ ГОТОВО\nCommit: %s\nBackup: %s\n' "$TARGET_COMMIT" "$BACKUP_DIR"
printf 'LIVE config не изменялся и должен оставаться выключенным.\n'

#!/data/data/com.termux/files/usr/bin/bash
set -Eeuo pipefail

BACKUP_INPUT="${1:-}"
REPO="${GALKA_REPO_DIR:-$HOME/GalkaLive}"
BACKUP_PARENT="${GALKA_BACKUP_ROOT:-$HOME/GalkaLive-backups}"
CONFIG="${GALKA_LIVE_CONFIG:-${XDG_CONFIG_HOME:-$HOME/.config}/galka-live.env}"
STATE_DIR="${GALKA_STATE_DIR:-$HOME/.local/share/galka-live}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)-$$"

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

[[ -n "$BACKUP_INPUT" && -d "$BACKUP_INPUT" ]] || {
  echo "Usage: bash scripts/rollback-galka-live.sh <backup-directory>" >&2
  exit 2
}
BACKUP_REAL="$(cd "$BACKUP_INPUT" && pwd -P)"
BACKUP_ROOT_REAL="$(mkdir -p "$BACKUP_PARENT" && cd "$BACKUP_PARENT" && pwd -P)"
REPO_REAL="$(cd "$REPO" && pwd -P)"
if path_is_within "$BACKUP_ROOT_REAL" "$REPO_REAL"; then
  echo "Backup root должен находиться вне Git-репозитория." >&2
  exit 2
fi
if path_is_within "$CONFIG" "$REPO_REAL" || path_is_within "$STATE_DIR" "$REPO_REAL"; then
  echo "Config и state должны находиться вне Git-репозитория." >&2
  exit 2
fi
case "$BACKUP_REAL" in
  "$BACKUP_ROOT_REAL"/*) ;;
  *) echo "Backup находится вне разрешённого каталога: $BACKUP_ROOT_REAL" >&2; exit 2 ;;
esac

for required in previous-commit.txt repo-path.txt checksums.sha256; do
  [[ -f "$BACKUP_REAL/$required" ]] || { echo "Backup неполон: $required" >&2; exit 2; }
done
(cd "$BACKUP_REAL" && sha256sum --check --quiet checksums.sha256)
[[ "$(<"$BACKUP_REAL/repo-path.txt")" == "$REPO" ]] || {
  echo "Backup принадлежит другому пути репозитория." >&2
  exit 2
}
PREVIOUS_COMMIT="$(<"$BACKUP_REAL/previous-commit.txt")"
[[ "$PREVIOUS_COMMIT" =~ ^[0-9a-f]{40,64}$ ]] || { echo "Некорректный commit в backup" >&2; exit 2; }

cd "$REPO"
git cat-file -e "$PREVIOUS_COMMIT^{commit}"
[[ -z "$(git status --porcelain=v1 --untracked-files=all)" ]] || {
  echo "Rollback требует чистое рабочее дерево." >&2
  exit 1
}
if command -v pgrep >/dev/null 2>&1 && pgrep -f 'python[^ ]* .*\-m live\.server' >/dev/null 2>&1; then
  echo "Сначала останови Galka LIVE через Ctrl+C." >&2
  exit 1
fi
if [[ -f "$CONFIG" ]] && grep -Eq '^[[:space:]]*HL_LIVE_ENABLED[[:space:]]*=[[:space:]]*YES[[:space:]]*$' "$CONFIG"; then
  echo "Rollback разрешён только при HL_LIVE_ENABLED=NO." >&2
  exit 1
fi
python3 scripts/check-repository-secrets.py --history

umask 077
PRE_ROLLBACK="$BACKUP_ROOT_REAL/pre-rollback-$STAMP"
mkdir -p "$PRE_ROLLBACK"
chmod 700 "$PRE_ROLLBACK"
git rev-parse HEAD > "$PRE_ROLLBACK/previous-commit.txt"
if [[ -d "$STATE_DIR" && ! -L "$STATE_DIR" ]]; then
  mkdir -p "$PRE_ROLLBACK/$(dirname "${STATE_DIR#/}")"
  mv "$STATE_DIR" "$PRE_ROLLBACK/$(dirname "${STATE_DIR#/}")/"
fi

ROLLBACK_BRANCH="rollback/galka-live-$STAMP"
git switch -c "$ROLLBACK_BRANCH" "$PREVIOUS_COMMIT"
if [[ -d "$BACKUP_REAL/state" ]]; then
  mkdir -p "$(dirname "$STATE_DIR")"
  cp -a "$BACKUP_REAL/state" "$STATE_DIR"
fi

if [[ "${GALKA_SKIP_DEPENDENCIES:-0}" != "1" ]]; then
  bash scripts/setup-galka-live.sh --dependencies-only
fi
bash scripts/verify-galka-live.sh

printf '\nROLLBACK ГОТОВ\nCommit: %s\nЛокальная ветка: %s\n' "$PREVIOUS_COMMIT" "$ROLLBACK_BRANCH"
printf 'Состояние до rollback сохранено в: %s\n' "$PRE_ROLLBACK"
printf 'Config не изменялся; LIVE должен оставаться выключенным.\n'

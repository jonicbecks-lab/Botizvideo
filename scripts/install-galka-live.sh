#!/data/data/com.termux/files/usr/bin/bash
set -Eeuo pipefail

DESTINATION="${GALKA_INSTALL_DIR:-$HOME/GalkaLive}"
REMOTE_URL="${GALKA_INSTALL_REMOTE:-https://github.com/jonicbecks-lab/Botizvideo.git}"
BRANCH="agent/galka-live-hardening-v3"

[[ "$DESTINATION" != "/" && "$DESTINATION" != "$HOME" ]] || {
  echo "Небезопасный путь установки: $DESTINATION" >&2
  exit 2
}
[[ ! -e "$DESTINATION" ]] || {
  echo "Путь уже существует: $DESTINATION" >&2
  exit 2
}
if [[ "${GALKA_ALLOW_LOCAL_REMOTE:-0}" != "1" ]]; then
  [[ "$REMOTE_URL" == "https://github.com/jonicbecks-lab/Botizvideo.git" ]] || {
    echo "Разрешён только официальный репозиторий Botizvideo." >&2
    exit 2
  }
fi

PARENT="$(dirname "$DESTINATION")"
mkdir -p "$PARENT"
STAGING="$(mktemp -d "$PARENT/.galka-install.XXXXXX")"
cleanup() {
  if [[ -d "$STAGING" ]]; then
    rm -rf "$STAGING"
  fi
}
trap cleanup EXIT INT TERM

git clone --branch "$BRANCH" --single-branch "$REMOTE_URL" "$STAGING/repo"
cd "$STAGING/repo"
[[ "$(git branch --show-current)" == "$BRANCH" ]]
python3 scripts/check-repository-secrets.py --history
git diff --quiet
git diff --cached --quiet

mv "$STAGING/repo" "$DESTINATION"
if [[ "${GALKA_SOURCE_ONLY:-0}" != "1" ]]; then
  bash "$DESTINATION/scripts/setup-galka-live.sh" --non-interactive
fi

trap - EXIT INT TERM
rmdir "$STAGING"
printf '\nGalka LIVE установлена: %s\nВетка: %s\n' "$DESTINATION" "$BRANCH"
printf 'LIVE выключен по умолчанию. Следующий шаг: cd %q && bash scripts/verify-galka-live.sh\n' "$DESTINATION"

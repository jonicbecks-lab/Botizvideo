#!/data/data/com.termux/files/usr/bin/bash
# shellcheck disable=SC1091
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/galka-live-common.sh"

galka_live_require_running

if [[ -L "$GALKA_LIVE_TOKEN_FILE" || ! -f "$GALKA_LIVE_TOKEN_FILE" ]]; then
  echo "СТОП: защищённый токен LIVE отсутствует."
  exit 1
fi
token="$(<"$GALKA_LIVE_TOKEN_FILE")"
if [[ ${#token} -lt 32 ]]; then
  echo "СТОП: защищённый токен LIVE повреждён."
  exit 1
fi
url="${GALKA_LIVE_URL}#token=${token}"

if command -v termux-open-url >/dev/null 2>&1; then
  termux-open-url "$url" >/dev/null 2>&1
elif command -v am >/dev/null 2>&1; then
  am start -a android.intent.action.VIEW -d "$url" >/dev/null 2>&1
else
  echo "Не найден способ открыть браузер из Termux."
  exit 1
fi

unset token url
echo "Galka LIVE открыта через защищённую ссылку. Доступ сохранится в HttpOnly cookie."

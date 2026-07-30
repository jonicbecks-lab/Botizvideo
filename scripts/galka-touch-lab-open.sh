#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

# shellcheck source=galka-touch-lab-common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/galka-touch-lab-common.sh"

touch_lab_require_running

if [[ -L "$TOUCH_LAB_TOKEN_FILE" || ! -f "$TOUCH_LAB_TOKEN_FILE" ]]; then
  echo "СТОП: защищённый токен Touch Lab отсутствует."
  exit 1
fi

token="$(<"$TOUCH_LAB_TOKEN_FILE")"
if [[ ${#token} -lt 32 ]]; then
  echo "СТОП: защищённый токен Touch Lab повреждён."
  exit 1
fi
url="${TOUCH_LAB_URL}#token=${token}"

if command -v termux-open-url >/dev/null 2>&1; then
  termux-open-url "$url" >/dev/null 2>&1
elif command -v am >/dev/null 2>&1; then
  am start -a android.intent.action.VIEW -d "$url" >/dev/null 2>&1
else
  echo "Не найден способ открыть браузер из Termux."
  exit 1
fi

unset token url
echo "Touch Lab открыт через защищённую ссылку. Токен сохранится в HttpOnly cookie."

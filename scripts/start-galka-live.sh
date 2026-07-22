#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$ROOT_DIR/.venv-live"
CONFIG_FILE="${GALKA_LIVE_CONFIG:-$HOME/.config/galka-live.env}"

cd "$ROOT_DIR"

if [[ ! -x "$VENV/bin/python" ]]; then
  echo "Galka LIVE ещё не настроена. Выполни:"
  echo "bash scripts/setup-galka-live.sh"
  exit 1
fi
if [[ ! -f "$CONFIG_FILE" ]]; then
  echo "Не найден секретный config: $CONFIG_FILE"
  echo "Выполни bash scripts/setup-galka-live.sh"
  exit 1
fi
chmod 600 "$CONFIG_FILE"

PORT="$(awk -F= '/^GALKA_PORT=/{gsub(/[[:space:]]/,"",$2);print $2;exit}' "$CONFIG_FILE")"
PORT="${PORT:-8098}"
LOG_FILE="${TMPDIR:-/tmp}/galka-live-${PORT}.log"
umask 077
rm -f "$LOG_FILE"
: > "$LOG_FILE"
chmod 600 "$LOG_FILE"

cleanup(){
  if [[ -n "${SERVER_PID:-}" ]] && kill -0 "$SERVER_PID" 2>/dev/null; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  rm -f "$LOG_FILE"
}
trap cleanup EXIT INT TERM

export PYTHONPATH="$ROOT_DIR"
export GALKA_LIVE_CONFIG="$CONFIG_FILE"
"$VENV/bin/python" -m live.server >"$LOG_FILE" 2>&1 &
SERVER_PID=$!

for _ in 1 2 3 4 5 6 7 8; do
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "Galka LIVE не запустилась:"
    cat "$LOG_FILE"
    exit 1
  fi
  if grep -q "Galka LIVE URL:" "$LOG_FILE" 2>/dev/null; then
    break
  fi
  sleep 1
done

if ! grep -q "Galka LIVE URL:" "$LOG_FILE" 2>/dev/null; then
  echo "Сервер не подтвердил запуск. Лог:"
  cat "$LOG_FILE"
  exit 1
fi

# Do not echo the session token into the terminal history. The browser receives
# it through the URL fragment, which is then removed by terminal/live.js.
sed '/^Galka LIVE URL: /d' "$LOG_FILE"
URL="$(sed -n 's/^Galka LIVE URL: //p' "$LOG_FILE" | tail -n 1)"
if [[ -z "$URL" ]]; then
  echo "Не удалось получить защищённый URL сессии."
  exit 1
fi
echo
echo "Открываю защищённую локальную сессию."
echo "Чтобы остановить LIVE-сервер, вернись в Termux и нажми Ctrl+C."

if command -v termux-open-url >/dev/null 2>&1 && termux-open-url "$URL"; then
  :
else
  echo "Автоматически открыть браузер не удалось."
  echo "Открой адрес вручную: $URL"
fi

wait "$SERVER_PID"

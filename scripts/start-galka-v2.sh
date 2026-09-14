#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

umask 077
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_FILE="${GALKA_LIVE_CONFIG:-$HOME/.config/galka-v2-test.env}"
VENV_PY="$ROOT_DIR/.venv-live/bin/python"
RUNTIME_DIR="${XDG_RUNTIME_DIR:-$HOME/.local/run}/galka-v2"
PID_FILE="$RUNTIME_DIR/server.pid"
LOG_FILE="$RUNTIME_DIR/server.log"
URL_FILE="$RUNTIME_DIR/bootstrap.url"

mkdir -p "$RUNTIME_DIR"
chmod 700 "$RUNTIME_DIR"

open_browser() {
  local url="$1"
  if command -v termux-open-url >/dev/null 2>&1; then
    termux-open-url "$url" >/dev/null 2>&1 &
    return 0
  fi
  if command -v am >/dev/null 2>&1; then
    am start -a android.intent.action.VIEW -d "$url" >/dev/null 2>&1 || true
    return 0
  fi
  return 1
}

if [[ -f "$PID_FILE" ]]; then
  pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
    if [[ -s "$URL_FILE" ]]; then
      if open_browser "$(cat "$URL_FILE")"; then
        echo "GALKA V2 уже работает. Браузер открыт автоматически."
      else
        echo "GALKA V2 уже работает, но Android URL opener не найден."
      fi
      exit 0
    fi
    echo "GALKA V2 уже работает, но bootstrap URL не найден. Останови её и запусти снова."
    exit 1
  fi
  rm -f "$PID_FILE" "$URL_FILE"
fi

if [[ ! -x "$VENV_PY" ]]; then
  echo "Не найдено окружение GALKA V2. Сначала: bash scripts/setup-galka-live.sh --dependencies-only" >&2
  exit 1
fi
if [[ ! -f "$CONFIG_FILE" ]]; then
  echo "Не найден config: $CONFIG_FILE" >&2
  exit 1
fi

: > "$LOG_FILE"
chmod 600 "$LOG_FILE"
rm -f "$URL_FILE"

cd "$ROOT_DIR"
nohup env \
  PYTHONPATH="$ROOT_DIR" \
  GALKA_LIVE_CONFIG="$CONFIG_FILE" \
  "$VENV_PY" -u -m live.server \
  >"$LOG_FILE" 2>&1 </dev/null &
pid=$!
printf '%s\n' "$pid" > "$PID_FILE"
chmod 600 "$PID_FILE"

url=""
for _ in $(seq 1 120); do
  if ! kill -0 "$pid" 2>/dev/null; then
    echo "GALKA V2 не запустилась. Последние строки лога:" >&2
    tail -n 30 "$LOG_FILE" >&2 || true
    rm -f "$PID_FILE" "$URL_FILE"
    exit 1
  fi
  if line="$(grep -m1 '^Galka V2 URL: ' "$LOG_FILE" 2>/dev/null)"; then
    url="${line#Galka V2 URL: }"
    break
  fi
  sleep 0.1
done

if [[ -z "$url" ]]; then
  echo "GALKA V2 запустилась, но URL не появился вовремя. Лог: $LOG_FILE" >&2
  exit 1
fi

printf '%s\n' "$url" > "$URL_FILE"
chmod 600 "$URL_FILE"

if open_browser "$url"; then
  echo "GALKA V2 запущена. Браузер открыт автоматически."
else
  echo "GALKA V2 запущена, но автоматическое открытие браузера недоступно." >&2
  echo "Установи/проверь termux-open-url и запусти этот скрипт ещё раз." >&2
  exit 1
fi

grep -E '^Режим:|^GALKA V2:' "$LOG_FILE" || true

#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$ROOT_DIR/.venv-live"
CONFIG_FILE="${GALKA_LIVE_CONFIG:-${XDG_CONFIG_HOME:-$HOME/.config}/galka-live.env}"

cd "$ROOT_DIR"

if [[ ! -x "$VENV/bin/python" ]]; then
  echo "GALKA MEM ещё не настроена. Выполни: bash scripts/setup-galka-live.sh"
  exit 1
fi
if [[ ! -f "$CONFIG_FILE" || -L "$CONFIG_FILE" ]]; then
  echo "Не найден безопасный LIVE config: $CONFIG_FILE" >&2
  exit 1
fi

PORT="$(PYTHONPATH="$ROOT_DIR" "$VENV/bin/python" -c 'from live.config import load_config; print(load_config().port)')"
if [[ ! "$PORT" =~ ^[0-9]+$ ]] || (( PORT < 1024 || PORT > 65535 )); then
  echo "Некорректный GALKA_PORT" >&2
  exit 1
fi

LOG_FILE="${TMPDIR:-/tmp}/galka-mem-${PORT}.log"
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

for _ in 1 2 3 4 5 6 7 8 9 10; do
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "GALKA MEM не запустилась:"
    sed '/^Galka .* URL: /d' "$LOG_FILE"
    exit 1
  fi
  if grep -q "Galka MEM URL:" "$LOG_FILE" 2>/dev/null; then
    break
  fi
  sleep 1
done

if ! grep -q "Galka MEM URL:" "$LOG_FILE" 2>/dev/null; then
  echo "Сервер не выдал GALKA MEM URL."
  sed '/^Galka .* URL: /d' "$LOG_FILE"
  exit 1
fi

sed '/^Galka .* URL: /d' "$LOG_FILE"
URL="$(sed -n 's/^Galka MEM URL: //p' "$LOG_FILE" | tail -n 1)"
if [[ -z "$URL" ]]; then
  echo "Не удалось получить защищённый GALKA MEM URL." >&2
  exit 1
fi

echo
echo "Открываю GALKA MEM. Для остановки вернись в Termux и нажми Ctrl+C."
if command -v termux-open-url >/dev/null 2>&1 && termux-open-url "$URL"; then
  :
else
  echo "Открой адрес вручную: $URL"
fi

wait "$SERVER_PID"

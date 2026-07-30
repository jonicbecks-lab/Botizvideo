#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${GALKA_TOUCH_LAB_PORT:-8099}"
HOST="127.0.0.1"
URL="http://${HOST}:${PORT}/touch-lab.html"
PYTHON_BIN="$ROOT_DIR/.venv-live/bin/python"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="$(command -v python || true)"
fi
if [[ -z "$PYTHON_BIN" ]]; then
  echo "Python не найден. Установи: pkg install python"
  exit 1
fi

LOG_FILE="${TMPDIR:-/tmp}/galka-touch-lab-${PORT}.log"

"$PYTHON_BIN" -m http.server "$PORT" --bind "$HOST" --directory "$ROOT_DIR/terminal" \
  >"$LOG_FILE" 2>&1 &
SERVER_PID=$!

cleanup() {
  kill "$SERVER_PID" 2>/dev/null || true
  wait "$SERVER_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

for _ in $(seq 1 30); do
  if kill -0 "$SERVER_PID" 2>/dev/null; then
    if "$PYTHON_BIN" - "$HOST" "$PORT" <<'PY' >/dev/null 2>&1
import socket
import sys

with socket.create_connection((sys.argv[1], int(sys.argv[2])), timeout=0.2):
    pass
PY
    then
      break
    fi
  else
    echo "Touch Lab не запустился. Лог: $LOG_FILE"
    exit 1
  fi
  sleep 0.1
done

echo "Galka Touch Lab: $URL"
echo "Это отдельный синтетический график. Hyperliquid и реальные ордера не подключены."

if command -v termux-open-url >/dev/null 2>&1; then
  termux-open-url "$URL" >/dev/null 2>&1 || true
elif command -v am >/dev/null 2>&1; then
  am start -a android.intent.action.VIEW -d "$URL" >/dev/null 2>&1 || true
fi

wait "$SERVER_PID"

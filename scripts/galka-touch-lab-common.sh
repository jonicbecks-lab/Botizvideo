#!/data/data/com.termux/files/usr/bin/bash
# shellcheck disable=SC2034

TOUCH_LAB_ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOUCH_LAB_HOST="127.0.0.1"
TOUCH_LAB_PORT="${GALKA_TOUCH_LAB_PORT:-8099}"
TOUCH_LAB_STATE_DIR="${GALKA_TOUCH_LAB_STATE_DIR:-$HOME/.local/state/galka-touch-lab}"
TOUCH_LAB_PID_FILE="$TOUCH_LAB_STATE_DIR/server.pid"
TOUCH_LAB_TOKEN_FILE="$TOUCH_LAB_STATE_DIR/session.token"
TOUCH_LAB_LOG_FILE="$TOUCH_LAB_STATE_DIR/server.log"
TOUCH_LAB_LOCK_DIR="$TOUCH_LAB_STATE_DIR/start.lock"
TOUCH_LAB_URL="http://${TOUCH_LAB_HOST}:${TOUCH_LAB_PORT}/touch-lab.html"

mkdir -p "$TOUCH_LAB_STATE_DIR"
chmod 700 "$TOUCH_LAB_STATE_DIR"

find_touch_lab_python() {
  local candidate="$TOUCH_LAB_ROOT_DIR/.venv-live/bin/python"
  if [[ -x "$candidate" ]]; then
    printf '%s\n' "$candidate"
    return 0
  fi
  command -v python
}

touch_lab_pid() {
  [[ -f "$TOUCH_LAB_PID_FILE" ]] || return 1
  local pid
  pid="$(tr -cd '0-9' < "$TOUCH_LAB_PID_FILE")"
  [[ -n "$pid" ]] || return 1
  printf '%s\n' "$pid"
}

touch_lab_process_alive() {
  local pid
  pid="$(touch_lab_pid 2>/dev/null || true)"
  [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

touch_lab_health() {
  local python_bin
  python_bin="$(find_touch_lab_python 2>/dev/null || true)"
  [[ -n "$python_bin" ]] || return 1
  "$python_bin" - "$TOUCH_LAB_HOST" "$TOUCH_LAB_PORT" <<'PY' >/dev/null 2>&1
import json
import sys
import urllib.request

host, port = sys.argv[1], int(sys.argv[2])
with urllib.request.urlopen(f"http://{host}:{port}/healthz", timeout=0.5) as response:
    payload = json.load(response)
if response.status != 200 or payload.get("server") != "touch-lab":
    raise SystemExit(1)
PY
}

touch_lab_require_running() {
  if ! touch_lab_process_alive || ! touch_lab_health; then
    echo "Touch Lab не запущен. Выполни: bash scripts/galka-touch-lab-start.sh"
    return 1
  fi
}

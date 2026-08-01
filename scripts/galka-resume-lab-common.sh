#!/data/data/com.termux/files/usr/bin/bash
# shellcheck disable=SC2034

RESUME_LAB_ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RESUME_LAB_HOST="127.0.0.1"
RESUME_LAB_PORT="${GALKA_RESUME_LAB_PORT:-8101}"
RESUME_LAB_STATE_DIR="${GALKA_RESUME_LAB_STATE_DIR:-$HOME/.local/state/galka-resume-lab}"
RESUME_LAB_PID_FILE="$RESUME_LAB_STATE_DIR/server.pid"
RESUME_LAB_LOG_FILE="$RESUME_LAB_STATE_DIR/server.log"
RESUME_LAB_LOCK_DIR="$RESUME_LAB_STATE_DIR/start.lock"
RESUME_LAB_URL="http://${RESUME_LAB_HOST}:${RESUME_LAB_PORT}/resume-lab.html"

mkdir -p "$RESUME_LAB_STATE_DIR"
chmod 700 "$RESUME_LAB_STATE_DIR"

resume_lab_python() {
  local candidate="$RESUME_LAB_ROOT_DIR/.venv-live/bin/python"
  if [[ -x "$candidate" ]]; then
    printf '%s\n' "$candidate"
    return 0
  fi
  command -v python
}

resume_lab_pid() {
  [[ -f "$RESUME_LAB_PID_FILE" ]] || return 1
  local pid
  pid="$(tr -cd '0-9' < "$RESUME_LAB_PID_FILE")"
  [[ -n "$pid" ]] || return 1
  printf '%s\n' "$pid"
}

resume_lab_process_alive() {
  local pid
  pid="$(resume_lab_pid 2>/dev/null || true)"
  [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

resume_lab_health() {
  local python_bin
  python_bin="$(resume_lab_python 2>/dev/null || true)"
  [[ -n "$python_bin" ]] || return 1
  "$python_bin" - "$RESUME_LAB_HOST" "$RESUME_LAB_PORT" <<'PY' >/dev/null 2>&1
import json
import sys
import urllib.request

host, port = sys.argv[1], int(sys.argv[2])
with urllib.request.urlopen(f"http://{host}:{port}/healthz", timeout=0.7) as response:
    payload = json.load(response)
if response.status != 200 or payload.get("mode") != "READ_ONLY":
    raise SystemExit(1)
PY
}

resume_lab_require_running() {
  if ! resume_lab_process_alive || ! resume_lab_health; then
    echo "Galka Resume Lab не запущена. Выполни: bash scripts/galka-resume-lab-start.sh"
    return 1
  fi
}

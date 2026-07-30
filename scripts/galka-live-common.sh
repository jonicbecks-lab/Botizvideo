#!/data/data/com.termux/files/usr/bin/bash
# shellcheck disable=SC2034

GALKA_LIVE_ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GALKA_LIVE_VENV="$GALKA_LIVE_ROOT_DIR/.venv-live"
GALKA_LIVE_CONFIG_FILE="${GALKA_LIVE_CONFIG:-${XDG_CONFIG_HOME:-$HOME/.config}/galka-live.env}"

if [[ ! -x "$GALKA_LIVE_VENV/bin/python" ]]; then
  echo "Galka LIVE ещё не настроена. Выполни: bash scripts/setup-galka-live.sh"
  return 1 2>/dev/null || exit 1
fi
if [[ ! -f "$GALKA_LIVE_CONFIG_FILE" || -L "$GALKA_LIVE_CONFIG_FILE" ]]; then
  echo "Не найден безопасный config: $GALKA_LIVE_CONFIG_FILE"
  return 1 2>/dev/null || exit 1
fi

mapfile -t GALKA_LIVE_CONFIG_VALUES < <(
  PYTHONPATH="$GALKA_LIVE_ROOT_DIR" GALKA_LIVE_CONFIG="$GALKA_LIVE_CONFIG_FILE" \
    "$GALKA_LIVE_VENV/bin/python" - <<'PY'
from live.config import load_config
config = load_config()
print(config.host)
print(config.port)
print(config.data_dir)
PY
)

GALKA_LIVE_HOST="${GALKA_LIVE_CONFIG_VALUES[0]:-}"
GALKA_LIVE_PORT="${GALKA_LIVE_CONFIG_VALUES[1]:-}"
GALKA_LIVE_DATA_DIR="${GALKA_LIVE_CONFIG_VALUES[2]:-}"
if [[ "$GALKA_LIVE_HOST" != "127.0.0.1" ]]; then
  echo "СТОП: LIVE должен слушать только 127.0.0.1"
  return 1 2>/dev/null || exit 1
fi
if [[ ! "$GALKA_LIVE_PORT" =~ ^[0-9]+$ ]] || (( GALKA_LIVE_PORT < 1024 || GALKA_LIVE_PORT > 65535 )); then
  echo "СТОП: некорректный GALKA_PORT"
  return 1 2>/dev/null || exit 1
fi

GALKA_LIVE_RUNTIME_DIR="$GALKA_LIVE_DATA_DIR/runtime"
GALKA_LIVE_PID_FILE="$GALKA_LIVE_RUNTIME_DIR/server.pid"
GALKA_LIVE_TOKEN_FILE="$GALKA_LIVE_RUNTIME_DIR/browser-session.token"
GALKA_LIVE_LOG_FILE="$GALKA_LIVE_RUNTIME_DIR/server.log"
GALKA_LIVE_LOCK_DIR="$GALKA_LIVE_RUNTIME_DIR/start.lock"
GALKA_LIVE_URL="http://${GALKA_LIVE_HOST}:${GALKA_LIVE_PORT}/terminal/live.html"

mkdir -p "$GALKA_LIVE_RUNTIME_DIR"
chmod 700 "$GALKA_LIVE_RUNTIME_DIR"

 galka_live_pid() {
  [[ -f "$GALKA_LIVE_PID_FILE" ]] || return 1
  local pid
  pid="$(tr -cd '0-9' < "$GALKA_LIVE_PID_FILE")"
  [[ -n "$pid" ]] || return 1
  printf '%s\n' "$pid"
}

 galka_live_process_alive() {
  local pid
  pid="$(galka_live_pid 2>/dev/null || true)"
  [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

 galka_live_health() {
  "$GALKA_LIVE_VENV/bin/python" - "$GALKA_LIVE_HOST" "$GALKA_LIVE_PORT" <<'PY' >/dev/null 2>&1
import json
import sys
import urllib.request

host, port = sys.argv[1], int(sys.argv[2])
with urllib.request.urlopen(f"http://{host}:{port}/healthz", timeout=0.7) as response:
    payload = json.load(response)
if response.status != 200 or payload.get("server") != "galka-live":
    raise SystemExit(1)
PY
}

 galka_live_require_running() {
  if ! galka_live_process_alive || ! galka_live_health; then
    echo "Galka LIVE не запущена. Выполни: bash scripts/galka-live-start.sh"
    return 1
  fi
}

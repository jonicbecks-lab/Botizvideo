#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

umask 077
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_FILE="${GALKA_LIVE_CONFIG:-$HOME/.config/galka-v2-test.env}"
PYTHON="$ROOT_DIR/.venv-live/bin/python"
RUNTIME_DIR="${XDG_RUNTIME_DIR:-$HOME/.local/run}/galka-v2"
PID_FILE="$RUNTIME_DIR/server.pid"

if [[ ! -x "$PYTHON" || ! -f "$CONFIG_FILE" || -L "$CONFIG_FILE" ]]; then
  echo "Не найден безопасный config/окружение GALKA V2." >&2
  exit 1
fi

"$PYTHON" - "$CONFIG_FILE" <<'PY'
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
values = {
    "HL_LIVE_ENABLED": "NO",
    "HL_LIVE_CONFIRM": "NOT_CONFIRMED",
}
for key, value in values.items():
    pattern = rf"^{re.escape(key)}\s*=.*$"
    if re.search(pattern, text, flags=re.M):
        text = re.sub(pattern, f"{key}={value}", text, flags=re.M)
    else:
        text += f"\n{key}={value}"
path.write_text(text.rstrip() + "\n", encoding="utf-8")
PY
chmod 600 "$CONFIG_FILE"

if [[ -f "$PID_FILE" ]]; then
  pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
    cmdline="$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)"
    if [[ "$cmdline" == *"live.galka_v2_server_entry"* ]]; then
      kill "$pid" 2>/dev/null || true
      for _ in $(seq 1 50); do
        kill -0 "$pid" 2>/dev/null || break
        sleep 0.1
      done
    else
      echo "PID-файл GALKA V2 указывает не на V2-процесс; чужой процесс не трогаю." >&2
      exit 1
    fi
  fi
  rm -f "$PID_FILE" "$RUNTIME_DIR/server.rev" "$RUNTIME_DIR/server.port"
fi

export GALKA_LIVE_CONFIG="$CONFIG_FILE"
exec bash "$ROOT_DIR/scripts/start-galka-v2.sh"

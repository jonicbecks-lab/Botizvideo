#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

umask 077
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE_CONFIG="${GALKA_LIVE_CONFIG:-$HOME/.config/galka-v2-test.env}"
VENV_PY="$ROOT_DIR/.venv-live/bin/python"
RUNTIME_DIR="${XDG_RUNTIME_DIR:-$HOME/.local/run}/galka-v2"
RUNTIME_CONFIG="$RUNTIME_DIR/runtime.env"
PID_FILE="$RUNTIME_DIR/server.pid"
REV_FILE="$RUNTIME_DIR/server.rev"
PORT_FILE="$RUNTIME_DIR/server.port"
LOG_FILE="$RUNTIME_DIR/server.log"

mkdir -p "$RUNTIME_DIR"
chmod 700 "$RUNTIME_DIR"

open_browser() {
  local url="$1"
  if command -v termux-open-url >/dev/null 2>&1; then
    termux-open-url "$url" >/dev/null 2>&1 &
    return 0
  fi
  if command -v am >/dev/null 2>&1; then
    am start -a android.intent.action.VIEW -d "$url" >/dev/null 2>&1
    return 0
  fi
  return 1
}

managed_pid_alive() {
  local pid="$1"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  local cmdline=""
  if [[ -r "/proc/$pid/cmdline" ]]; then
    cmdline="$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)"
  fi
  [[ "$cmdline" == *"live.galka_v2_server_entry"* ]]
}

server_health() {
  local port="$1"
  "$VENV_PY" - "$port" <<'PY' >/dev/null 2>&1
import sys
import urllib.request

port = int(sys.argv[1])
with urllib.request.urlopen(
    f"http://127.0.0.1:{port}/terminal/live.html",
    timeout=0.8,
) as response:
    body = response.read(4096)
    if response.status != 200 or b"GALKA" not in body:
        raise SystemExit(1)
PY
}

stop_managed_pid() {
  local pid="$1"
  kill "$pid" 2>/dev/null || true
  for _ in $(seq 1 40); do
    if ! kill -0 "$pid" 2>/dev/null; then
      return 0
    fi
    sleep 0.1
  done
  kill -9 "$pid" 2>/dev/null || true
}

if [[ ! -x "$VENV_PY" ]]; then
  echo "Не найдено окружение GALKA V2." >&2
  echo "Сначала выполни: bash scripts/setup-galka-live.sh --dependencies-only" >&2
  exit 1
fi
if [[ ! -f "$SOURCE_CONFIG" ]]; then
  echo "Не найден config: $SOURCE_CONFIG" >&2
  exit 1
fi

CURRENT_REV="$(git -C "$ROOT_DIR" rev-parse HEAD 2>/dev/null || printf 'unknown')"

if [[ -f "$PID_FILE" ]]; then
  old_pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if managed_pid_alive "$old_pid"; then
    old_rev="$(cat "$REV_FILE" 2>/dev/null || true)"
    old_port="$(cat "$PORT_FILE" 2>/dev/null || true)"
    if [[ "$old_rev" == "$CURRENT_REV" && "$old_port" =~ ^[0-9]+$ ]] && server_health "$old_port"; then
      if open_browser "http://127.0.0.1:${old_port}/open"; then
        echo "GALKA V2 уже работает на порту $old_port. Браузер открыт автоматически."
        grep -E '^Режим:|^GALKA V2:' "$LOG_FILE" 2>/dev/null || true
        exit 0
      fi
      echo "GALKA V2 работает, но Android не открыл браузер." >&2
      exit 1
    fi
    echo "Перезапускаю старый процесс GALKA V2 после обновления кода..."
    stop_managed_pid "$old_pid"
  fi
  rm -f "$PID_FILE" "$REV_FILE" "$PORT_FILE"
fi

preferred_port="$($VENV_PY - "$SOURCE_CONFIG" <<'PY'
from pathlib import Path
import re
import sys

text = Path(sys.argv[1]).read_text(encoding="utf-8")
match = re.search(r"^GALKA_PORT\s*=\s*([0-9]+)\s*$", text, flags=re.M)
port = int(match.group(1)) if match else 3003
print(port if 1024 <= port <= 65535 else 3003)
PY
)"

port="$($VENV_PY - "$preferred_port" <<'PY'
import socket
import sys

preferred = int(sys.argv[1])
candidates = [preferred]
for value in range(3003, 3100):
    if value not in candidates:
        candidates.append(value)

for port in candidates:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
    except OSError:
        sock.close()
        continue
    sock.close()
    print(port)
    raise SystemExit(0)
raise SystemExit(1)
PY
)" || {
  echo "Не найден свободный локальный порт 3003-3099." >&2
  exit 1
}

cp "$SOURCE_CONFIG" "$RUNTIME_CONFIG"
chmod 600 "$RUNTIME_CONFIG"
PORT="$port" "$VENV_PY" - "$RUNTIME_CONFIG" <<'PY'
from pathlib import Path
import os
import re
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
port = os.environ["PORT"]
pattern = r"^GALKA_PORT\s*=.*$"
if re.search(pattern, text, flags=re.M):
    text = re.sub(pattern, f"GALKA_PORT={port}", text, flags=re.M)
else:
    text += f"\nGALKA_PORT={port}\n"
path.write_text(text, encoding="utf-8")
PY
chmod 600 "$RUNTIME_CONFIG"

: > "$LOG_FILE"
chmod 600 "$LOG_FILE"

cd "$ROOT_DIR"
nohup env \
  PYTHONPATH="$ROOT_DIR" \
  GALKA_LIVE_CONFIG="$RUNTIME_CONFIG" \
  "$VENV_PY" -u -m live.galka_v2_server_entry \
  >"$LOG_FILE" 2>&1 </dev/null &
pid=$!
printf '%s\n' "$pid" > "$PID_FILE"
printf '%s\n' "$CURRENT_REV" > "$REV_FILE"
printf '%s\n' "$port" > "$PORT_FILE"
chmod 600 "$PID_FILE" "$REV_FILE" "$PORT_FILE"

ready=0
for _ in $(seq 1 120); do
  if ! kill -0 "$pid" 2>/dev/null; then
    echo "GALKA V2 не запустилась. Последние строки лога:" >&2
    tail -n 40 "$LOG_FILE" >&2 || true
    rm -f "$PID_FILE" "$REV_FILE" "$PORT_FILE"
    exit 1
  fi
  if server_health "$port"; then
    ready=1
    break
  fi
  sleep 0.1
done

if [[ "$ready" != "1" ]]; then
  echo "GALKA V2 не ответила вовремя. Последние строки лога:" >&2
  tail -n 40 "$LOG_FILE" >&2 || true
  stop_managed_pid "$pid"
  rm -f "$PID_FILE" "$REV_FILE" "$PORT_FILE"
  exit 1
fi

if ! open_browser "http://127.0.0.1:${port}/open"; then
  echo "GALKA V2 запущена, но Android URL opener недоступен." >&2
  echo "Проверь пакет Termux:API/termux-open-url." >&2
  exit 1
fi

echo "GALKA V2 запущена на порту $port. Браузер открыт автоматически."
grep -E '^Сеть:|^Режим:|^GALKA V2:' "$LOG_FILE" || true

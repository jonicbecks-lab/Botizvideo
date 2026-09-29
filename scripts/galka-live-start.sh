#!/data/data/com.termux/files/usr/bin/bash
# shellcheck disable=SC1091,SC2317
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/galka-live-common.sh"

if ! mkdir "$GALKA_LIVE_LOCK_DIR" 2>/dev/null; then
  echo "Galka LIVE уже запускается в другой сессии. Подожди несколько секунд."
  exit 1
fi
cleanup_lock() {
  rmdir "$GALKA_LIVE_LOCK_DIR" 2>/dev/null || true
}
trap cleanup_lock EXIT INT TERM

if galka_live_process_alive && galka_live_health; then
  echo "Galka LIVE уже работает на порту $GALKA_LIVE_PORT."
  if [[ "${GALKA_LIVE_NO_OPEN:-0}" != "1" ]]; then
    bash "$GALKA_LIVE_ROOT_DIR/scripts/galka-live-open.sh"
  fi
  exit 0
fi

if galka_live_process_alive; then
  echo "СТОП: процесс LIVE существует, но сервер не отвечает."
  echo "Проверь: bash scripts/galka-live-status.sh"
  exit 1
fi
rm -f "$GALKA_LIVE_PID_FILE"

: > "$GALKA_LIVE_LOG_FILE"
chmod 600 "$GALKA_LIVE_LOG_FILE"

if command -v termux-wake-lock >/dev/null 2>&1; then
  if termux-wake-lock >/dev/null 2>&1; then
    touch "$GALKA_LIVE_RUNTIME_DIR/wake-lock.enabled"
    chmod 600 "$GALKA_LIVE_RUNTIME_DIR/wake-lock.enabled"
  fi
fi

server_command=(
  env
  "PYTHONPATH=$GALKA_LIVE_ROOT_DIR"
  "GALKA_LIVE_CONFIG=$GALKA_LIVE_CONFIG_FILE"
  "GALKA_LIVE_SESSION_TOKEN_FILE=$GALKA_LIVE_TOKEN_FILE"
  "GALKA_LIVE_PID_FILE=$GALKA_LIVE_PID_FILE"
  "$GALKA_LIVE_VENV/bin/python"
  -m
  live.research_server_entry
)

if command -v setsid >/dev/null 2>&1; then
  nohup setsid "${server_command[@]}" >> "$GALKA_LIVE_LOG_FILE" 2>&1 < /dev/null &
else
  nohup "${server_command[@]}" >> "$GALKA_LIVE_LOG_FILE" 2>&1 < /dev/null &
fi
launcher_pid=$!

for _ in $(seq 1 80); do
  if galka_live_process_alive && galka_live_health; then
    sed -n '/^Сеть:/p;/^Режим:/p;/^Плечо:/p' "$GALKA_LIVE_LOG_FILE"
    echo "Galka LIVE запущена в фоне: $GALKA_LIVE_URL"
    echo "Сессию Termux можно закрыть; монитор продолжит работу, пока Android не остановит сам Termux."
    if [[ "${GALKA_LIVE_NO_OPEN:-0}" != "1" ]]; then
      bash "$GALKA_LIVE_ROOT_DIR/scripts/galka-live-open.sh"
    fi
    exit 0
  fi
  if ! kill -0 "$launcher_pid" 2>/dev/null && ! galka_live_process_alive; then
    break
  fi
  sleep 0.1
done

kill "$launcher_pid" 2>/dev/null || true
rm -f "$GALKA_LIVE_PID_FILE"
echo "Galka LIVE не запустилась. Последние строки лога:"
tail -n 30 "$GALKA_LIVE_LOG_FILE" 2>/dev/null || true
exit 1

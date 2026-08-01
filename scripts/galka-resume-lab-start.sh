#!/data/data/com.termux/files/usr/bin/bash
# shellcheck disable=SC1091,SC2317
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/galka-resume-lab-common.sh"

if ! mkdir "$RESUME_LAB_LOCK_DIR" 2>/dev/null; then
  echo "Galka Resume Lab уже запускается. Подожди несколько секунд."
  exit 1
fi
cleanup_lock() {
  rmdir "$RESUME_LAB_LOCK_DIR" 2>/dev/null || true
}
trap cleanup_lock EXIT INT TERM

if resume_lab_process_alive && resume_lab_health; then
  echo "Galka Resume Lab уже работает на порту $RESUME_LAB_PORT."
  bash "$RESUME_LAB_ROOT_DIR/scripts/galka-resume-lab-open.sh"
  exit 0
fi

if resume_lab_process_alive; then
  echo "СТОП: процесс Resume Lab существует, но сервер не отвечает."
  echo "Проверь: bash scripts/galka-resume-lab-status.sh"
  exit 1
fi
rm -f "$RESUME_LAB_PID_FILE"

python_bin="$(resume_lab_python 2>/dev/null || true)"
if [[ -z "$python_bin" ]]; then
  echo "Python не найден. Установи: pkg install python"
  exit 1
fi

: > "$RESUME_LAB_LOG_FILE"
chmod 600 "$RESUME_LAB_LOG_FILE"

if command -v termux-wake-lock >/dev/null 2>&1; then
  if termux-wake-lock >/dev/null 2>&1; then
    touch "$RESUME_LAB_STATE_DIR/wake-lock.enabled"
    chmod 600 "$RESUME_LAB_STATE_DIR/wake-lock.enabled"
  fi
fi

server_command=(
  env
  "GALKA_RESUME_LAB_PORT=$RESUME_LAB_PORT"
  "$python_bin"
  -m
  live.resume_lab_server
)

if command -v setsid >/dev/null 2>&1; then
  nohup setsid "${server_command[@]}" >> "$RESUME_LAB_LOG_FILE" 2>&1 < /dev/null &
else
  nohup "${server_command[@]}" >> "$RESUME_LAB_LOG_FILE" 2>&1 < /dev/null &
fi
launcher_pid=$!
printf '%s\n' "$launcher_pid" > "$RESUME_LAB_PID_FILE"
chmod 600 "$RESUME_LAB_PID_FILE"

for _ in $(seq 1 60); do
  if resume_lab_process_alive && resume_lab_health; then
    echo "Galka Resume Lab запущена: $RESUME_LAB_URL"
    echo "Режим: READ ONLY. Ключи и торговые команды не используются."
    bash "$RESUME_LAB_ROOT_DIR/scripts/galka-resume-lab-open.sh"
    exit 0
  fi
  if ! kill -0 "$launcher_pid" 2>/dev/null; then
    break
  fi
  sleep 0.1
done

kill "$launcher_pid" 2>/dev/null || true
rm -f "$RESUME_LAB_PID_FILE"
echo "Galka Resume Lab не запустилась. Лог: $RESUME_LAB_LOG_FILE"
tail -n 30 "$RESUME_LAB_LOG_FILE" 2>/dev/null || true
exit 1

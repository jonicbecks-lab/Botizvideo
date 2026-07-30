#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

# shellcheck source=galka-touch-lab-common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/galka-touch-lab-common.sh"

if ! mkdir "$TOUCH_LAB_LOCK_DIR" 2>/dev/null; then
  echo "Touch Lab уже запускается в другой сессии. Подожди несколько секунд."
  exit 1
fi
cleanup_lock() {
  rmdir "$TOUCH_LAB_LOCK_DIR" 2>/dev/null || true
}
trap cleanup_lock EXIT INT TERM

if touch_lab_process_alive && touch_lab_health; then
  echo "Touch Lab уже работает на порту $TOUCH_LAB_PORT."
  bash "$TOUCH_LAB_ROOT_DIR/scripts/galka-touch-lab-open.sh"
  exit 0
fi

if touch_lab_process_alive; then
  echo "СТОП: процесс Touch Lab существует, но сервер не отвечает."
  echo "Проверь: bash scripts/galka-touch-lab-status.sh"
  exit 1
fi
rm -f "$TOUCH_LAB_PID_FILE"

python_bin="$(find_touch_lab_python 2>/dev/null || true)"
if [[ -z "$python_bin" ]]; then
  echo "Python не найден. Установи: pkg install python"
  exit 1
fi

if [[ -L "$TOUCH_LAB_TOKEN_FILE" ]]; then
  echo "СТОП: файл токена Touch Lab не должен быть символической ссылкой."
  exit 1
fi
if [[ ! -f "$TOUCH_LAB_TOKEN_FILE" ]]; then
  token_tmp="$TOUCH_LAB_STATE_DIR/session.token.tmp.$$"
  "$python_bin" - <<'PY' > "$token_tmp"
import secrets
print(secrets.token_urlsafe(48))
PY
  chmod 600 "$token_tmp"
  mv -f "$token_tmp" "$TOUCH_LAB_TOKEN_FILE"
fi
chmod 600 "$TOUCH_LAB_TOKEN_FILE"

: > "$TOUCH_LAB_LOG_FILE"
chmod 600 "$TOUCH_LAB_LOG_FILE"

if command -v termux-wake-lock >/dev/null 2>&1; then
  if termux-wake-lock >/dev/null 2>&1; then
    touch "$TOUCH_LAB_STATE_DIR/wake-lock.enabled"
    chmod 600 "$TOUCH_LAB_STATE_DIR/wake-lock.enabled"
  fi
fi

server_command=(
  "$python_bin"
  "$TOUCH_LAB_ROOT_DIR/scripts/galka-touch-lab-server.py"
  --root "$TOUCH_LAB_ROOT_DIR/terminal"
  --token-file "$TOUCH_LAB_TOKEN_FILE"
  --pid-file "$TOUCH_LAB_PID_FILE"
  --host "$TOUCH_LAB_HOST"
  --port "$TOUCH_LAB_PORT"
)

if command -v setsid >/dev/null 2>&1; then
  nohup setsid "${server_command[@]}" >> "$TOUCH_LAB_LOG_FILE" 2>&1 < /dev/null &
else
  nohup "${server_command[@]}" >> "$TOUCH_LAB_LOG_FILE" 2>&1 < /dev/null &
fi
launcher_pid=$!

for _ in $(seq 1 50); do
  if touch_lab_process_alive && touch_lab_health; then
    echo "Touch Lab запущен в фоне: http://${TOUCH_LAB_HOST}:${TOUCH_LAB_PORT}"
    echo "Сессию Termux теперь можно закрыть. Hyperliquid и реальные ордера не подключены."
    bash "$TOUCH_LAB_ROOT_DIR/scripts/galka-touch-lab-open.sh"
    exit 0
  fi
  if ! kill -0 "$launcher_pid" 2>/dev/null && ! touch_lab_process_alive; then
    break
  fi
  sleep 0.1
done

kill "$launcher_pid" 2>/dev/null || true
rm -f "$TOUCH_LAB_PID_FILE"
echo "Touch Lab не запустился. Лог: $TOUCH_LAB_LOG_FILE"
tail -n 20 "$TOUCH_LAB_LOG_FILE" 2>/dev/null || true
exit 1

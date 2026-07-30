#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

# shellcheck source=galka-touch-lab-common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/galka-touch-lab-common.sh"

pid="$(touch_lab_pid 2>/dev/null || true)"
if touch_lab_process_alive && touch_lab_health; then
  echo "Touch Lab: RUNNING"
  echo "PID: $pid"
  echo "Адрес: http://${TOUCH_LAB_HOST}:${TOUCH_LAB_PORT}/touch-lab.html"
  echo "Cookie-сессия проверяется внутри браузера."
  exit 0
fi

if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
  echo "Touch Lab: PROCESS EXISTS, SERVER NOT RESPONDING"
else
  echo "Touch Lab: STOPPED"
fi

if [[ -s "$TOUCH_LAB_LOG_FILE" ]]; then
  echo "Последние строки лога:"
  tail -n 20 "$TOUCH_LAB_LOG_FILE"
fi
exit 1

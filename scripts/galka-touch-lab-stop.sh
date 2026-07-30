#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

# shellcheck source=galka-touch-lab-common.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/galka-touch-lab-common.sh"

pid="$(touch_lab_pid 2>/dev/null || true)"
if [[ -z "$pid" ]] || ! kill -0 "$pid" 2>/dev/null; then
  rm -f "$TOUCH_LAB_PID_FILE"
  echo "Touch Lab уже остановлен."
  exit 0
fi

kill "$pid" 2>/dev/null || true
for _ in $(seq 1 50); do
  if ! kill -0 "$pid" 2>/dev/null; then
    break
  fi
  sleep 0.1
done
if kill -0 "$pid" 2>/dev/null; then
  kill -KILL "$pid" 2>/dev/null || true
fi
rm -f "$TOUCH_LAB_PID_FILE"

if [[ -f "$TOUCH_LAB_STATE_DIR/wake-lock.enabled" ]] && command -v termux-wake-unlock >/dev/null 2>&1; then
  termux-wake-unlock >/dev/null 2>&1 || true
  rm -f "$TOUCH_LAB_STATE_DIR/wake-lock.enabled"
fi

echo "Touch Lab остановлен. Постоянный cookie-токен сохранён для следующего запуска."

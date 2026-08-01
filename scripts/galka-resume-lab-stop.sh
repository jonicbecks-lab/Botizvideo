#!/data/data/com.termux/files/usr/bin/bash
# shellcheck disable=SC1091
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/galka-resume-lab-common.sh"

pid="$(resume_lab_pid 2>/dev/null || true)"
if [[ -z "$pid" ]] || ! kill -0 "$pid" 2>/dev/null; then
  rm -f "$RESUME_LAB_PID_FILE"
  echo "Galka Resume Lab уже остановлена."
  exit 0
fi

kill "$pid"
for _ in $(seq 1 40); do
  if ! kill -0 "$pid" 2>/dev/null; then
    rm -f "$RESUME_LAB_PID_FILE"
    if [[ -f "$RESUME_LAB_STATE_DIR/wake-lock.enabled" ]] && command -v termux-wake-unlock >/dev/null 2>&1; then
      termux-wake-unlock >/dev/null 2>&1 || true
      rm -f "$RESUME_LAB_STATE_DIR/wake-lock.enabled"
    fi
    echo "Galka Resume Lab остановлена."
    exit 0
  fi
  sleep 0.1
done

echo "Не удалось подтвердить остановку PID $pid."
exit 1

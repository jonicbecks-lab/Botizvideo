#!/data/data/com.termux/files/usr/bin/bash
# shellcheck disable=SC1091
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/galka-resume-lab-common.sh"

if resume_lab_process_alive && resume_lab_health; then
  echo "Galka Resume Lab: RUNNING"
  echo "URL: $RESUME_LAB_URL"
  echo "Режим: READ ONLY"
  echo "PID: $(resume_lab_pid)"
  exit 0
fi

echo "Galka Resume Lab: STOPPED"
if [[ -f "$RESUME_LAB_LOG_FILE" ]]; then
  echo "Последние строки лога:"
  tail -n 20 "$RESUME_LAB_LOG_FILE" || true
fi
exit 1

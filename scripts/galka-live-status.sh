#!/data/data/com.termux/files/usr/bin/bash
# shellcheck disable=SC1091
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/galka-live-common.sh"

pid="$(galka_live_pid 2>/dev/null || true)"
if galka_live_process_alive && galka_live_health; then
  echo "Galka LIVE: RUNNING"
  echo "PID: $pid"
  echo "Адрес: $GALKA_LIVE_URL"
  sed -n '/^Сеть:/p;/^Режим:/p;/^Плечо:/p' "$GALKA_LIVE_LOG_FILE" 2>/dev/null || true
  exit 0
fi

if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
  echo "Galka LIVE: PROCESS EXISTS, SERVER NOT RESPONDING"
else
  echo "Galka LIVE: STOPPED"
fi
if [[ -s "$GALKA_LIVE_LOG_FILE" ]]; then
  echo "Последние строки лога:"
  tail -n 30 "$GALKA_LIVE_LOG_FILE"
fi
exit 1

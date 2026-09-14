#!/data/data/com.termux/files/usr/bin/bash
# shellcheck disable=SC1091
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/galka-live-common.sh"

pid="$(galka_live_pid 2>/dev/null || true)"
if [[ -z "$pid" ]] || ! kill -0 "$pid" 2>/dev/null; then
  rm -f "$GALKA_LIVE_PID_FILE"
  echo "Galka LIVE уже остановлена."
  exit 0
fi

if [[ "${1:-}" != "STOP_GALKA_LIVE" ]]; then
  echo "СТОП: это выключит монитор кампании. Биржевые ордера останутся на Hyperliquid."
  echo "Для подтверждения выполни: bash scripts/galka-live-stop.sh STOP_GALKA_LIVE"
  exit 1
fi

kill "$pid" 2>/dev/null || true
for _ in $(seq 1 100); do
  if ! kill -0 "$pid" 2>/dev/null; then
    break
  fi
  sleep 0.1
done
if kill -0 "$pid" 2>/dev/null; then
  echo "Сервер не завершился корректно; отправляю принудительный сигнал."
  kill -KILL "$pid" 2>/dev/null || true
fi
rm -f "$GALKA_LIVE_PID_FILE"

if [[ -f "$GALKA_LIVE_RUNTIME_DIR/wake-lock.enabled" ]] && command -v termux-wake-unlock >/dev/null 2>&1; then
  termux-wake-unlock >/dev/null 2>&1 || true
  rm -f "$GALKA_LIVE_RUNTIME_DIR/wake-lock.enabled"
fi

echo "Galka LIVE остановлена. Постоянная cookie-сессия сохранена для следующего запуска."

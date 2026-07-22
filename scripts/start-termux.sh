#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# The launcher never switches branches, resets files, or touches browser data.
# This keeps feature-branch testing safe and leaves updates to the explicit git pull command.
if command -v git >/dev/null 2>&1 && git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  CURRENT_BRANCH="$(git branch --show-current || true)"
  if [[ -n "$CURRENT_BRANCH" && "$CURRENT_BRANCH" != "main" ]]; then
    echo "Galka запускается из ветки: $CURRENT_BRANCH"
  fi
fi

if ! command -v python >/dev/null 2>&1; then
  echo "Python не найден. Устанавливаю..."
  pkg update -y
  pkg install python -y
fi

REQUESTED_PORT="${1:-}"
if [[ -n "$REQUESTED_PORT" ]]; then
  PORT="$REQUESTED_PORT"
else
  PORT="$(python - <<'PY'
import socket
for port in range(8080, 8090):
    with socket.socket() as s:
        try:
            s.bind(('127.0.0.1', port))
        except OSError:
            continue
        print(port)
        break
else:
    raise SystemExit('Нет свободного порта 8080–8089')
PY
)"
fi
if [[ ! "$PORT" =~ ^[0-9]+$ ]] || (( PORT < 1024 || PORT > 65535 )); then
  echo "Порт должен быть целым числом от 1024 до 65535." >&2
  exit 2
fi

LOG_FILE="${TMPDIR:-/tmp}/galka-terminal-${PORT}.log"
SERVE_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/galka-terminal-root.XXXXXX")"
VERSION="$(git rev-parse --short HEAD 2>/dev/null || date +%s)"
URL="http://127.0.0.1:${PORT}/terminal/pro.html?v=${VERSION}"
umask 077
rm -f "$LOG_FILE"
: > "$LOG_FILE"
chmod 600 "$LOG_FILE"

cleanup() {
  if [[ -n "${SERVER_PID:-}" ]] && kill -0 "$SERVER_PID" 2>/dev/null; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  rm -f "$LOG_FILE"
  rm -rf -- "$SERVE_ROOT"
}
trap cleanup EXIT INT TERM

# Expose only browser assets and reviewed result packs. Serving the repository root would also
# expose Git metadata and any unrelated untracked files to other local processes or browser tabs.
cp -R "$ROOT_DIR/terminal" "$ROOT_DIR/results" "$SERVE_ROOT/"
python -m http.server "$PORT" --bind 127.0.0.1 --directory "$SERVE_ROOT" >"$LOG_FILE" 2>&1 &
SERVER_PID=$!
sleep 1

if ! kill -0 "$SERVER_PID" 2>/dev/null; then
  echo "Не удалось запустить терминал. Лог: $LOG_FILE"
  cat "$LOG_FILE"
  exit 1
fi

echo "Galka Pro запущен: $URL"
echo "BTC, ETH и SOL используют публичные данные Binance USD-M Futures."
echo "Все сделки виртуальные. API-ключи и реальные ордера отсутствуют."
echo "Paper-бот работает, пока браузерная вкладка открыта."
echo "Чтобы остановить сервер, вернись в Termux и нажми Ctrl+C."

if command -v termux-open-url >/dev/null 2>&1; then
  termux-open-url "$URL" || true
else
  echo "Открой адрес вручную в браузере: $URL"
fi

wait "$SERVER_PID"

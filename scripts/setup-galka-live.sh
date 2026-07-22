#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$ROOT_DIR/.venv-live"
REQUIREMENTS="$ROOT_DIR/live/requirements-termux.txt"
DEFAULT_CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}"
CONFIG_FILE="${GALKA_LIVE_CONFIG:-$DEFAULT_CONFIG_DIR/galka-live.env}"
CONFIG_DIR="$(dirname "$CONFIG_FILE")"
MODE="interactive"

case "${1:-}" in
  "") ;;
  --dependencies-only) MODE="dependencies" ;;
  --non-interactive) MODE="non-interactive" ;;
  *) echo "Usage: bash scripts/setup-galka-live.sh [--dependencies-only|--non-interactive]" >&2; exit 2 ;;
esac

umask 077
cd "$ROOT_DIR"

if ! command -v python >/dev/null 2>&1; then
  if ! command -v pkg >/dev/null 2>&1; then
    echo "Python не найден; установи Python 3.12+ и повтори запуск." >&2
    exit 1
  fi
  pkg update -y
  pkg install -y python
fi

if command -v pkg >/dev/null 2>&1; then
  # Termux has no PyPI wheels for several native dependencies.
  pkg install -y clang make pkg-config libffi openssl
  if [[ "$MODE" == "interactive" ]] && ! command -v nano >/dev/null 2>&1; then
    pkg install -y nano
  fi
fi

LOCK_HASH="$(python - "$REQUIREMENTS" <<'PY'
from hashlib import sha256
from pathlib import Path
import sys
print(sha256(Path(sys.argv[1]).read_bytes()).hexdigest())
PY
)"
LOCK_MARKER="$VENV/.galka-requirements-sha256"

if [[ -e "$VENV" && ! -x "$VENV/bin/python" ]]; then
  BROKEN_VENV="$ROOT_DIR/.venv-live.broken-$(date +%Y%m%d-%H%M%S)"
  echo "Сохраняю незавершённое окружение как $BROKEN_VENV"
  mv "$VENV" "$BROKEN_VENV"
fi
if [[ -x "$VENV/bin/python" ]] && \
   ! "$VENV/bin/python" -c 'import hyperliquid, eth_account, eth_utils' >/dev/null 2>&1; then
  BROKEN_VENV="$ROOT_DIR/.venv-live.broken-$(date +%Y%m%d-%H%M%S)"
  echo "Сохраняю повреждённое окружение как $BROKEN_VENV"
  mv "$VENV" "$BROKEN_VENV"
fi
if [[ -x "$VENV/bin/python" ]] && \
   [[ ! -f "$LOCK_MARKER" || "$(<"$LOCK_MARKER")" != "$LOCK_HASH" ]]; then
  BROKEN_VENV="$ROOT_DIR/.venv-live.previous-$(date +%Y%m%d-%H%M%S)"
  echo "Lock зависимостей изменился; сохраняю прежнее окружение как $BROKEN_VENV"
  mv "$VENV" "$BROKEN_VENV"
fi
if [[ ! -x "$VENV/bin/python" ]]; then
  echo "Создаю отдельное окружение Galka LIVE..."
  python -m venv "$VENV"
fi

"$VENV/bin/python" -m pip install --upgrade \
  packaging==26.2 pip==26.1.2 setuptools==83.0.0 wheel==0.47.0
"$VENV/bin/python" -m pip install --no-cache-dir -r "$REQUIREMENTS"
"$VENV/bin/python" "$ROOT_DIR/scripts/check-python-lock.py"
printf '%s\n' "$LOCK_HASH" > "$LOCK_MARKER"

if command -v git >/dev/null 2>&1 && git -C "$ROOT_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  git -C "$ROOT_DIR" config core.hooksPath .githooks
fi

if [[ "$MODE" == "dependencies" ]]; then
  echo "Зависимости LIVE установлены; секретный config не изменялся."
  exit 0
fi

if python - "$ROOT_DIR" "$CONFIG_FILE" <<'PY'
from pathlib import Path
import sys
try:
    Path(sys.argv[2]).expanduser().resolve(strict=False).relative_to(Path(sys.argv[1]).resolve())
except ValueError:
    raise SystemExit(1)
PY
then
  echo "Каталог конфигурации должен находиться вне Git-репозитория." >&2
  exit 1
fi
if [[ -L "$CONFIG_DIR" ]]; then
  echo "Каталог конфигурации не должен быть symlink: $CONFIG_DIR" >&2
  exit 1
fi
mkdir -p "$CONFIG_DIR"
chmod 700 "$CONFIG_DIR"
if [[ -L "$CONFIG_FILE" ]]; then
  echo "Секретный config не должен быть symlink: $CONFIG_FILE" >&2
  exit 1
fi
if [[ ! -e "$CONFIG_FILE" ]]; then
  : > "$CONFIG_FILE"
  chmod 600 "$CONFIG_FILE"
  cat > "$CONFIG_FILE" <<'EOF'
# Public address of the MAIN Hyperliquid account that owns the funds.
HL_ACCOUNT_ADDRESS=0xPASTE_MAIN_ACCOUNT_ADDRESS

# Private key of a separately approved API Wallet / Agent Wallet.
# Never use the seed phrase or private key of the main wallet.
HL_API_SECRET_KEY=0xPASTE_API_WALLET_PRIVATE_KEY

HL_MAINNET=true
HL_LEVERAGE=10
HL_ISOLATED=true
HL_TOTAL_NOTIONAL=200
HL_MAX_MARGIN_FRACTION=0.60
HL_REQUEST_TIMEOUT=8
HL_MONITOR_INTERVAL=6
HL_GLOBAL_CHECK_INTERVAL=30
HL_MAKER_FEE_RATE=0.00015
HL_TAKER_FEE_RATE=0.00045

# LIVE requires both exact values. Keep these defaults during validation.
HL_LIVE_ENABLED=NO
HL_LIVE_CONFIRM=NOT_CONFIRMED

GALKA_HOST=127.0.0.1
GALKA_PORT=8098
EOF
fi
chmod 600 "$CONFIG_FILE"

cat <<EOF

Локальный секретный файл: $CONFIG_FILE

Замени только адрес основного аккаунта и private key отдельного API Wallet.
Оставь HL_LIVE_ENABLED=NO и HL_LIVE_CONFIRM=NOT_CONFIRMED до завершения проверок.
Не присылай содержимое файла и не добавляй его в Git.
EOF

if [[ "$MODE" == "interactive" ]]; then
  if ! command -v nano >/dev/null 2>&1; then
    echo "nano не найден; открой файл локальным редактором." >&2
    exit 1
  fi
  nano "$CONFIG_FILE"
  chmod 600 "$CONFIG_FILE"
fi

echo
echo "Настройка завершена. Безопасная проверка с LIVE OFF:"
echo "bash scripts/verify-galka-live.sh"
echo "bash scripts/check-galka-live-account.sh"

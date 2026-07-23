#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$ROOT_DIR/.venv-live"
CONFIG_FILE="${GALKA_LIVE_CONFIG:-${XDG_CONFIG_HOME:-$HOME/.config}/galka-live.env}"

if [[ ! -x "$VENV/bin/python" ]]; then
  echo "Сначала выполни: bash scripts/setup-galka-live.sh"
  exit 1
fi
if [[ ! -f "$CONFIG_FILE" ]]; then
  echo "Не найден config: $CONFIG_FILE"
  exit 1
fi
chmod 600 "$CONFIG_FILE"

cd "$ROOT_DIR"
export PYTHONPATH="$ROOT_DIR"
export GALKA_LIVE_CONFIG="$CONFIG_FILE"
exec "$VENV/bin/python" scripts/check-galka-live-account.py

#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

bash scripts/galka-live-10-usd-test.sh --enable

CONFIG_FILE="${GALKA_LIVE_CONFIG:-${XDG_CONFIG_HOME:-$HOME/.config}/galka-live.env}"
PYTHON_BIN="$ROOT_DIR/.venv-live/bin/python"
if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="$(command -v python)"
fi

PYTHONPATH="$ROOT_DIR" "$PYTHON_BIN" - "$CONFIG_FILE" <<'PY'
from pathlib import Path
import sys

from live.config import load_config

config = load_config(Path(sys.argv[1]))
if not config.live_enabled:
    raise SystemExit("LIVE не включён")
if config.leverage != 10:
    raise SystemExit("Плечо должно быть 10x")
if abs(config.total_notional - 100.0) > 1e-9:
    raise SystemExit("Номинал GALKA должен быть $100")
print("LIVE-профиль подтверждён: $100 номинал · 10x isolated · около $10 маржи")
PY

exec bash scripts/start-galka-live.sh

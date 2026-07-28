#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEFAULT_CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}"
CONFIG_FILE="${GALKA_LIVE_CONFIG:-$DEFAULT_CONFIG_DIR/galka-live.env}"
MODE="${1:---prepare}"

case "$MODE" in
  --prepare|--enable|--disable) ;;
  *)
    echo "Usage: bash scripts/galka-live-10-usd-test.sh [--prepare|--enable|--disable]" >&2
    exit 2
    ;;
esac

if [[ ! -f "$CONFIG_FILE" || -L "$CONFIG_FILE" ]]; then
  echo "Не найден безопасный LIVE config: $CONFIG_FILE" >&2
  echo "Сначала выполни: bash scripts/setup-galka-live.sh --non-interactive" >&2
  exit 1
fi

if [[ "$MODE" == "--enable" ]]; then
  cat <<'EOF'

Будут разрешены РЕАЛЬНЫЕ ордера Hyperliquid со следующим профилем:
  номинал GALKA: не более $100
  плечо: 10x isolated
  расчётная маржа: не более $10

Это не гарантирует максимальный убыток ровно $10: возможны комиссии, funding,
проскальзывание при аварийном закрытии и биржевые риски.
EOF
  printf 'Для продолжения введи ENABLE_10_USD_LIVE_TEST: '
  read -r confirmation
  if [[ "$confirmation" != "ENABLE_10_USD_LIVE_TEST" ]]; then
    echo "Отмена: LIVE не включён."
    exit 1
  fi
fi

umask 077
python - "$CONFIG_FILE" "$MODE" <<'PY'
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

path = Path(sys.argv[1]).expanduser().absolute()
mode = sys.argv[2]
contents = path.read_text(encoding="utf-8")

updates = {
    "HL_LEVERAGE": "10",
    "HL_TOTAL_NOTIONAL": "100",
    "HL_LIVE_ENABLED": "YES" if mode == "--enable" else "NO",
    "HL_LIVE_CONFIRM": "I_UNDERSTAND_REAL_MONEY" if mode == "--enable" else "NOT_CONFIRMED",
}

seen: set[str] = set()
out: list[str] = []
for raw in contents.splitlines():
    stripped = raw.strip()
    if stripped and not stripped.startswith("#") and "=" in stripped:
        key = stripped.split("=", 1)[0].strip()
        if key in updates:
            if key in seen:
                raise SystemExit(f"Duplicate config key: {key}")
            out.append(f"{key}={updates[key]}")
            seen.add(key)
            continue
    out.append(raw)

if out and out[-1] != "":
    out.append("")
for key, value in updates.items():
    if key not in seen:
        out.append(f"{key}={value}")

payload = "\n".join(out).rstrip() + "\n"
fd, temp_name = tempfile.mkstemp(prefix=".galka-live.env.", dir=path.parent)
try:
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        fd = -1
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp_name, path)
    os.chmod(path, 0o600)
finally:
    if fd >= 0:
        os.close(fd)
    try:
        os.unlink(temp_name)
    except FileNotFoundError:
        pass
PY

PYTHON_BIN="$ROOT_DIR/.venv-live/bin/python"
if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="$(command -v python)"
fi

PYTHONPATH="$ROOT_DIR" "$PYTHON_BIN" - "$CONFIG_FILE" "$MODE" <<'PY'
from pathlib import Path
import sys

from live.config import load_config

config = load_config(Path(sys.argv[1]))
mode = sys.argv[2]
if config.leverage != 10 or abs(config.total_notional - 100.0) > 1e-9:
    raise SystemExit("Тестовый профиль не прошёл проверку")
if mode == "--enable" and not config.live_enabled:
    raise SystemExit("LIVE не включился")
if mode != "--enable" and config.live_enabled:
    raise SystemExit("LIVE должен оставаться выключенным")

status = "LIVE ON" if config.live_enabled else "LIVE OFF"
print(f"Профиль проверен: $100 номинал · 10x isolated · до $10 расчётной маржи · {status}")
PY

if [[ "$MODE" == "--enable" ]]; then
  echo "Перезапусти Галку. Реальные ордера всё равно потребуют «Проверить» и финального подтверждения."
elif [[ "$MODE" == "--disable" ]]; then
  echo "LIVE выключен. Профиль \$100 / 10x сохранён."
else
  echo "Профиль подготовлен, но LIVE оставлен выключенным."
fi

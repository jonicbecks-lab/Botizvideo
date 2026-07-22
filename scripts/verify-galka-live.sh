#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$ROOT_DIR/.venv-live"

cd "$ROOT_DIR"

if [[ ! -x "$VENV/bin/python" ]]; then
  echo "Не найдено окружение $VENV"
  echo "Сначала выполни: bash scripts/setup-galka-live.sh"
  exit 1
fi

echo "[1/5] Проверка pinned-зависимостей"
"$VENV/bin/python" - <<'PY'
from importlib.metadata import version

expected = {
    "hyperliquid-python-sdk": "0.24.0",
    "eth-account": "0.10.0",
    "eth-utils": "4.1.1",
    "eth-abi": "5.2.0",
    "eth-keyfile": "0.8.1",
}
for package, wanted in expected.items():
    actual = version(package)
    if actual != wanted:
        raise SystemExit(f"{package}: установлено {actual}, требуется {wanted}")
print("Зависимости совпадают.")
PY

echo "[2/5] Компиляция Python"
PYTHONPATH="$ROOT_DIR" "$VENV/bin/python" -m compileall -q live tests

echo "[3/5] Python-тесты LIVE"
PYTHONPATH="$ROOT_DIR" "$VENV/bin/python" -m unittest discover -s tests -v

echo "[4/5] Проверка shell-скриптов"
for script in scripts/*.sh; do
  bash -n "$script"
done

echo "[5/5] Проверка браузерного терминала"
if command -v node >/dev/null 2>&1; then
  node scripts/check-live-terminal.mjs
else
  echo "Node.js не установлен — статическая JS-проверка пропущена."
fi

echo
echo "VERIFY PASS: локальные тесты Galka LIVE завершены без ошибок."

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

echo "[1/6] Проверка полного lock зависимостей"
"$VENV/bin/python" scripts/check-python-lock.py

echo "[2/6] Компиляция Python"
PYTHONPATH="$ROOT_DIR" "$VENV/bin/python" -m compileall -q live tests

echo "[3/6] Python-тесты LIVE"
PYTHONPATH="$ROOT_DIR" "$VENV/bin/python" -m unittest discover -s tests -v

echo "[4/6] Проверка секретов в tracked Git и истории"
"$VENV/bin/python" scripts/check-repository-secrets.py --history

echo "[5/6] Проверка shell-скриптов"
for script in scripts/*.sh; do
  bash -n "$script"
done

echo "[6/6] Проверка браузерного терминала"
if command -v node >/dev/null 2>&1; then
  node scripts/check-live-terminal.mjs
else
  echo "Node.js не установлен — статическая JS-проверка пропущена."
fi

echo
echo "VERIFY PASS: локальные тесты Galka LIVE завершены без ошибок."

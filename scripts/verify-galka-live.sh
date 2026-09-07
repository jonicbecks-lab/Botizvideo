#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$ROOT_DIR/.venv-live"
FULL_HISTORY="${GALKA_VERIFY_FULL_HISTORY:-0}"

cd "$ROOT_DIR"

if [[ ! -x "$VENV/bin/python" ]]; then
  echo "Не найдено окружение $VENV"
  echo "Сначала выполни: bash scripts/setup-galka-live.sh"
  exit 1
fi

echo "[1/7] Проверка полного lock зависимостей"
"$VENV/bin/python" scripts/check-python-lock.py

echo "[2/7] Компиляция Python"
PYTHONPATH="$ROOT_DIR" "$VENV/bin/python" -m compileall -q live tests

echo "[3/7] Python-тесты LIVE"
PYTHONPATH="$ROOT_DIR" "$VENV/bin/python" -m unittest discover -s tests -v

if [[ "$FULL_HISTORY" == "1" ]]; then
  echo "[4/7] FULL: проверка секретов в tracked Git и всей истории"
  "$VENV/bin/python" scripts/check-repository-secrets.py --history
else
  echo "[4/7] FAST: проверка секретов только в текущих tracked-файлах"
  "$VENV/bin/python" scripts/check-repository-secrets.py
fi

echo "[5/7] Аудит GitHub workflows"
"$VENV/bin/python" scripts/check-workflows.py

echo "[6/7] Проверка shell-скриптов"
for script in scripts/*.sh; do
  bash -n "$script"
done
if command -v shellcheck >/dev/null 2>&1; then
  shellcheck -x scripts/*.sh .githooks/pre-commit
else
  echo "ShellCheck не установлен локально — строгая shell-проверка остаётся обязательной в CI."
fi

echo "[7/7] Проверка браузерного терминала"
if command -v node >/dev/null 2>&1; then
  node scripts/check-live-terminal.mjs
else
  echo "Node.js не установлен — статическая JS-проверка пропущена."
fi

echo
if [[ "$FULL_HISTORY" == "1" ]]; then
  echo "VERIFY PASS: полный локальный аудит Galka LIVE завершён без ошибок."
else
  echo "VERIFY PASS: быстрая локальная проверка Galka LIVE завершена без ошибок."
  echo "Полный аудит Git-истории при необходимости: bash scripts/verify-galka-live-full.sh"
fi

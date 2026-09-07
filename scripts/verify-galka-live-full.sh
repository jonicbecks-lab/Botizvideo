#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

bash scripts/verify-galka-live.sh

echo
echo "[FULL] Проверка секретов в tracked Git и всей истории"
"$ROOT_DIR/.venv-live/bin/python" scripts/check-repository-secrets.py --history

echo
echo "FULL VERIFY PASS: включая аудит секретов всей Git-истории."

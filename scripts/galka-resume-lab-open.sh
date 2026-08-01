#!/data/data/com.termux/files/usr/bin/bash
# shellcheck disable=SC1091
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/galka-resume-lab-common.sh"
resume_lab_require_running

if command -v termux-open-url >/dev/null 2>&1; then
  termux-open-url "$RESUME_LAB_URL" >/dev/null 2>&1 || true
else
  am start -a android.intent.action.VIEW -d "$RESUME_LAB_URL" >/dev/null 2>&1 || true
fi

echo "$RESUME_LAB_URL"

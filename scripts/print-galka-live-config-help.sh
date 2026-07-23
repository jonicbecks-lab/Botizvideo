#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail
CONFIG_FILE="${GALKA_LIVE_CONFIG:-${XDG_CONFIG_HOME:-$HOME/.config}/galka-live.env}"
echo "Секретный файл: $CONFIG_FILE"
echo "Открыть: nano $CONFIG_FILE"
echo "Права: chmod 600 $CONFIG_FILE"
echo "Не присылай содержимое файла и не делай его скриншот."

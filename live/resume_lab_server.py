from __future__ import annotations

import json
import mimetypes
import os
import signal
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

HOST = "127.0.0.1"
PORT = int(os.environ.get("GALKA_RESUME_LAB_PORT", "8101"))
API_URL = "https://api.hyperliquid.xyz/info"
ROOT = Path(__file__).resolve().parents[1]
TERMINAL_DIR = ROOT / "terminal"

INTERVAL_MS = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "8h": 28_800_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
}
SUPPORTED_COINS = {"BTC", "ETH", "SOL"}
STATIC_FILES = {
    "/": TERMINAL_DIR / "resume-lab.html",
    "/resume-lab.html": TERMINAL_DIR / "resume-lab.html",
    "/resume-lab.css": TERMINAL_DIR / "resume-lab.css",
    "/resume-lab.js": TERMINAL_DIR / "resume-lab.js",
    "/live.css": TERMINAL_DIR / "live.css",
    "/live-chart.css": TERMINAL_DIR / "live-chart.css",
    "/icons/galka-mark.svg": TERMINAL_DIR / "icons" / "galka-mark.svg",
    "/vendor/galka-chart.js": TERMINAL_DIR / "vendor" / "galka-chart.js",
    "/vendor/galka-future-pan.js": TERMINAL_DIR / "vendor" / "galka-future-pan.js",
    "/vendor/galka-visibility-recovery.js": TERMINAL_DIR / "vendor" / "galka-visibility-recovery.js",
    "/vendor/galka-touch-actions.js": TERMINAL_DIR / "vendor" / "galka-touch-actions.js",
}


def _json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _positive_int(value: str | None, default: int, maximum: int) -> int:
    try:
        parsed = int(value or default)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(parsed, maximum))


def fetch_candles(coin: str, interval: str, limit: int) -> list[dict[str, Any]]:
    coin = coin.upper()
    if coin not in SUPPORTED_COINS:
        raise ValueError("Поддерживаются только BTC, ETH и SOL")
    if interval not in INTERVAL_MS:
        raise ValueError("Неподдерживаемый таймфрейм")

    limit = max(3, min(int(limit), 1000))
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - INTERVAL_MS[interval] * (limit + 5)
    body = _json_bytes(
        {
            "type": "candleSnapshot",
            "req": {
                "coin": coin,
                "interval": interval,
                "startTime": start_ms,
                "endTime": end_ms,
            },
        }
    )
    request = urllib.request.Request(
        API_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "Galka-Resume-Lab/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            raw = response.read(2_000_000)
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"Hyperliquid недоступен: {exc}") from exc

    rows = json.loads(raw.decode("utf-8"))
    if not isinstance(rows, list):
        raise RuntimeError("Hyperliquid вернул неожиданный ответ")

    output: list[dict[str, Any]] = []
    for row in rows[-limit:]:
        try:
            output.append(
                {
                    "time": int(row["t"]) // 1000,
                    "open": float(row["o"]),
                    "high": float(row["h"]),
                    "low": float(row["l"]),
                    "close": float(row["c"]),
                    "volume": float(row.get("v") or 0),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    return output


class ResumeLabHandler(BaseHTTPRequestHandler):
    server_version = "GalkaResumeLab/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[{time.strftime('%H:%M:%S')}] {self.address_string()} {fmt % args}", flush=True)

    def _send_bytes(self, payload: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(payload)

    def _send_json(self, payload: Any, status: int = 200) -> None:
        self._send_bytes(_json_bytes(payload), "application/json; charset=utf-8", status)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path == "/healthz":
            self._send_json({"ok": True, "mode": "READ_ONLY", "port": PORT})
            return

        if parsed.path == "/api/resume-lab/candles":
            query = urllib.parse.parse_qs(parsed.query)
            coin = (query.get("coin") or ["BTC"])[0]
            interval = (query.get("interval") or ["5m"])[0]
            limit = _positive_int((query.get("limit") or ["600"])[0], 600, 1000)
            try:
                candles = fetch_candles(coin, interval, limit)
            except ValueError as exc:
                self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
                return
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_GATEWAY)
                return
            self._send_json(
                {
                    "ok": True,
                    "data": candles,
                    "serverTime": int(time.time() * 1000),
                }
            )
            return

        path = STATIC_FILES.get(parsed.path)
        if path is None or not path.is_file():
            self._send_json({"ok": False, "error": "Not found"}, HTTPStatus.NOT_FOUND)
            return
        payload = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {"application/javascript", "image/svg+xml"}:
            content_type += "; charset=utf-8"
        self._send_bytes(payload, content_type)


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), ResumeLabHandler)

    def stop_server(_signum: int, _frame: Any) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop_server)
    signal.signal(signal.SIGINT, stop_server)
    print(f"Galka Resume Lab: http://{HOST}:{PORT}/resume-lab.html", flush=True)
    print("READ ONLY: ключи и торговые команды не используются", flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

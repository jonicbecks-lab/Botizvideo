import threading
import time

import requests

FAPI = "https://fapi.binance.com"
_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "super-paper-termux-chart/1.0"})
_CACHE = {}
_CACHE_LOCK = threading.Lock()


def _fetch_bars(symbol: str, interval: str, limit: int):
    key = (symbol, interval, limit)
    now = time.time()
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit and now - hit[0] < 3.0:
            return hit[1]
    r = _SESSION.get(
        FAPI + "/fapi/v1/klines",
        params={"symbol": symbol, "interval": interval, "limit": limit},
        timeout=15,
    )
    r.raise_for_status()
    now_ms = int(time.time() * 1000)
    bars = []
    for z in r.json():
        bars.append({
            "ts": int(z[0]),
            "o": float(z[1]),
            "h": float(z[2]),
            "l": float(z[3]),
            "c": float(z[4]),
            "v": float(z[5]),
            "closeTs": int(z[6]),
            "closed": int(z[6]) < now_ms,
        })
    with _CACHE_LOCK:
        _CACHE[key] = (now, bars)
    return bars


def chart_payload(account, interval: str):
    interval = interval.lower()
    if interval not in ("2h", "5m"):
        raise ValueError("interval must be 2h or 5m")
    limit = 140 if interval == "2h" else 180
    bars = _fetch_bars(account.symbol, interval, limit)
    with account.lock:
        pos = None
        if account.position:
            pos = {
                "side": account.position.get("side"),
                "avg": account.position.get("avg"),
                "liq": account.liq_price(),
                "profitStopArmed": bool(account.position.get("profitStopArmed")),
                "profitStopPrice": account.position.get("profitStopPrice"),
                "profitLockStep": account.position.get("profitLockStep", 0),
            }
        pending = [dict(x) for x in account.pending]
        events = []
        for e in account.events[-180:]:
            snap = e.get("snapshot") or {}
            events.append({
                "ts": e.get("ts"),
                "actor": e.get("actor"),
                "action": e.get("action"),
                "detail": e.get("detail", ""),
                "price": snap.get("price"),
            })
        payload = {
            "symbol": account.symbol,
            "interval": interval,
            "price": account.last_price,
            "signal": account.signal,
            "position": pos,
            "pending": pending,
            "events": events,
            "bars": bars,
            "serverTs": int(time.time() * 1000),
        }
    return payload

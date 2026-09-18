import math
import time
from typing import Dict, List, Tuple

import requests

DAY = 86_400_000
BAR2H = 7_200_000
H = 12
MEMORY_DAYS = 60

FAPI = "https://fapi.binance.com"
SPOT = "https://api.binance.com"

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "super-paper-termux/1.0"})


def _get(url: str, params: dict, timeout: int = 20):
    r = _SESSION.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _closed_klines(base: str, path: str, symbol: str, interval: str, start_ms: int, limit: int = 1000):
    now = int(time.time() * 1000)
    data = _get(base + path, {"symbol": symbol, "interval": interval, "startTime": start_ms, "limit": limit})
    out = []
    for z in data:
        close_ts = int(z[6])
        if close_ts >= now:
            continue
        qv = float(z[7]) if len(z) > 7 else 0.0
        tbq = float(z[10]) if len(z) > 10 else 0.0
        out.append({
            "ts": int(z[0]), "o": float(z[1]), "h": float(z[2]), "l": float(z[3]), "c": float(z[4]),
            "closeTs": close_ts, "qv": qv, "tbq": tbq,
        })
    return out


def fetch_rows(symbol: str, lookback_days: int = 75) -> List[dict]:
    now = int(time.time() * 1000)
    start = now - lookback_days * DAY
    fut = _closed_klines(FAPI, "/fapi/v1/klines", symbol, "2h", start, 1000)
    spot = _closed_klines(SPOT, "/api/v3/klines", symbol, "2h", start, 1000)
    prem_raw = _get(FAPI + "/fapi/v1/premiumIndexKlines", {
        "symbol": symbol, "interval": "2h", "startTime": start, "limit": 1000
    })
    prem = {int(z[0]): float(z[4]) for z in prem_raw if int(z[6]) < now}
    spot_map = {x["ts"]: x for x in spot}
    funding = fetch_funding_events(symbol, start)

    rows = []
    for z in fut:
        ss = spot_map.get(z["ts"])
        if not ss:
            continue
        close_ts = z["closeTs"]
        f24 = 0.0
        f72 = 0.0
        events = []
        lo72 = close_ts - 72 * 3_600_000
        lo24 = close_ts - 24 * 3_600_000
        for t, rate in funding:
            if t > close_ts:
                break
            if t >= lo72:
                f72 += rate
            if t >= lo24:
                f24 += rate
            if z["ts"] < t <= close_ts:
                events.append((t, rate))
        qv = z["qv"]
        sq = ss["qv"]
        rows.append({
            "ts": z["ts"], "o": z["o"], "h": z["h"], "l": z["l"], "c": z["c"],
            "closeTs": close_ts, "qv": qv, "perpDelta": 2.0 * z["tbq"] - qv,
            "spotQv": sq, "spotDelta": 2.0 * ss["tbq"] - sq,
            "premium": prem.get(z["ts"]), "funding24": f24, "funding72": f72,
            "fundingEvents": events,
        })
    return rows


def fetch_live_price(symbol: str) -> float:
    x = _get(FAPI + "/fapi/v2/ticker/price", {"symbol": symbol})
    return float(x["price"])


def fetch_funding_events(symbol: str, start_ms: int) -> List[Tuple[int, float]]:
    data = _get(FAPI + "/fapi/v1/fundingRate", {"symbol": symbol, "startTime": int(start_ms), "limit": 1000})
    out = []
    for x in data:
        try:
            out.append((int(x["fundingTime"]), float(x["fundingRate"])))
        except Exception:
            pass
    out.sort()
    return out


def fetch_closed_5m(symbol: str, start_ms: int) -> List[dict]:
    now = int(time.time() * 1000)
    out = []
    cursor = int(start_ms)
    for _ in range(12):
        data = _get(FAPI + "/fapi/v1/klines", {
            "symbol": symbol, "interval": "5m", "startTime": cursor, "limit": 1000
        })
        if not data:
            break
        progressed = False
        for z in data:
            ts = int(z[0]); close_ts = int(z[6])
            if close_ts >= now:
                continue
            out.append({"ts": ts, "o": float(z[1]), "h": float(z[2]), "l": float(z[3]), "c": float(z[4])})
            cursor = ts + 300_000
            progressed = True
        if not progressed or len(data) < 1000:
            break
    return out


def clip(x, a, b):
    return max(a, min(b, x))


def sigmoid(z):
    return 1.0 / (1.0 + math.exp(-clip(z, -30.0, 30.0)))


def dot(w, x):
    return sum(a * b for a, b in zip(w, x))


def safe_flow(a, i, n, kd, kq):
    d = q = 0.0
    c = 0
    for j in range(i - n + 1, i + 1):
        if j < 0:
            continue
        D = a[j].get(kd)
        Q = a[j].get(kq)
        if D is not None and Q:
            d += D; q += Q; c += 1
    return d / q if c >= math.ceil(n * 0.7) and q else None


def rv(a, i, n):
    if i < n:
        return None
    return math.sqrt(sum(math.log(a[j]["c"] / a[j - 1]["c"]) ** 2 for j in range(i - n + 1, i + 1)))


def efficiency(a, i, n):
    if i < n:
        return None
    den = sum(abs(math.log(a[j]["c"] / a[j - 1]["c"])) for j in range(i - n + 1, i + 1))
    return abs(math.log(a[i]["c"] / a[i - n]["c"])) / den if den else 0.0


def prem_avg(a, i, n):
    z = [a[j].get("premium") for j in range(max(0, i - n + 1), i + 1) if a[j].get("premium") is not None]
    return sum(z) / len(z) if z else None


def feat(a, i):
    if i < 36:
        return None
    r = a[i]
    ret = lambda n: math.log(r["c"] / a[i - n]["c"])
    pf = lambda n: safe_flow(a, i, n, "perpDelta", "qv")
    sf = lambda n: safe_flow(a, i, n, "spotDelta", "spotQv")
    vals = [pf(3), pf(6), pf(12), pf(36), sf(3), sf(6), sf(12), sf(36)]
    if any(x is None for x in vals):
        return None
    p6, p12, p24, p72, s6, s12, s24, s72 = vals
    vol24, vol72 = rv(a, i, 12), rv(a, i, 36)
    eff24, eff72 = efficiency(a, i, 12), efficiency(a, i, 36)
    pa, pa72 = prem_avg(a, i, 12), prem_avg(a, i, 36)
    q24 = sum(a[j]["qv"] for j in range(i - 11, i + 1))
    q7 = sum(a[j]["qv"] for j in range(max(0, i - 83), i + 1))
    volimp = math.log((q24 / 12) / (q7 / min(84, i + 1))) if q7 else 0.0
    return [
        clip(ret(3) / .025, -4, 4), clip(ret(6) / .04, -4, 4), clip(ret(12) / .06, -4, 4), clip(ret(36) / .11, -4, 4),
        clip(p6 / .08, -4, 4), clip(p12 / .08, -4, 4), clip(p24 / .08, -4, 4), clip(p72 / .08, -4, 4),
        clip(s6 / .08, -4, 4), clip(s12 / .08, -4, 4), clip(s24 / .08, -4, 4), clip(s72 / .08, -4, 4),
        clip((s12 - p12) / .08, -4, 4), clip((s24 - p24) / .08, -4, 4), clip((r.get("funding24") or 0) / .0015, -4, 4),
        clip((r.get("funding72") or 0) / .004, -4, 4), clip((pa or 0) / .0015, -4, 4), clip((pa72 or 0) / .0015, -4, 4),
        clip((vol24 or 0) / .05, -4, 4), clip((vol72 or 0) / .09, -4, 4), clip((eff24 or 0) - .35, -1, 1) * 2,
        clip((eff72 or 0) - .28, -1, 1) * 2, clip(volimp, -3, 3),
        clip((ret(12) / .04) - (p24 / .08) * .45, -4, 4), clip((ret(12) / .04) - (s24 / .08) * .55, -4, 4),
    ]


def bucket(a, i):
    f = feat(a, i)
    if f is None:
        return None
    sg = lambda x: 1 if x > .35 else -1 if x < -.35 else 0
    return (sg(f[2]), sg(f[6]), sg(f[10]), sg(f[14]), sg(f[16]), 1 if f[20] > .1 else 0)


def fit_window(a, end_idx, days=MEMORY_DAYS):
    win = days * 12
    w = [0.0] * 25
    bias = 0.0
    buckets: Dict[tuple, Tuple[float, float]] = {}
    samples = []
    for j in range(max(84, end_idx - win + 1), end_idx + 1):
        x = feat(a, j)
        if x is None or j + H >= len(a):
            continue
        y = 1.0 if a[j + H]["c"] > a[j]["c"] else 0.0
        samples.append((x, y))
        bk = bucket(a, j)
        if bk is not None:
            u, n = buckets.get(bk, (1.5, 3.0))
            buckets[bk] = (u + y, n + 1.0)
    if len(samples) < 60:
        return None
    for ep in range(6):
        lr = .045 / math.sqrt(1 + ep * .7)
        for x, y in samples:
            p = sigmoid(bias + dot(w, x))
            err = y - p
            bias += lr * err
            for k in range(25):
                w[k] += lr * (err * x[k] - .0022 * w[k])
    return w, bias, buckets


def build_adaptive(rows: List[dict], days=MEMORY_DAYS) -> List[dict]:
    a = [dict(r) for r in rows]
    mdl = None
    last_fit = -9999
    for i in range(len(a)):
        matured = i - H
        if matured >= 84 and (mdl is None or i - last_fit >= 12):
            mdl = fit_window(a, matured, days)
            last_fit = i
        x = feat(a, i)
        if x is not None and mdl is not None:
            w, bias, buckets = mdl
            pl = sigmoid(bias + dot(w, x))
            bk = bucket(a, i)
            bs = buckets.get(bk)
            pb = bs[0] / bs[1] if bs and bs[1] >= 8 else .5
            struct = sigmoid(.45 * x[2] + .35 * x[6] + .45 * x[10] - .12 * x[14] - .08 * x[16] + .10 * x[20])
            a[i]["pRaw"] = .68 * pl + .20 * pb + .12 * struct
        else:
            a[i]["pRaw"] = None
    finalize_state(a)
    return a


def finalize_state(a: List[dict]):
    sm = None
    st = 0
    held = 0
    opp = 0
    for i, r in enumerate(a):
        p = r.get("pRaw")
        if p is not None:
            sm = p if sm is None else .58 * p + .42 * sm
        r["p24"] = sm
        for n, h in [(3, 6), (6, 12), (12, 24)]:
            if i >= n:
                r["mom" + str(h)] = math.log(r["c"] / a[i - n]["c"])
                r["pf" + str(h)] = safe_flow(a, i, n, "perpDelta", "qv")
                r["sf" + str(h)] = safe_flow(a, i, n, "spotDelta", "spotQv")
            else:
                r["mom" + str(h)] = r["pf" + str(h)] = r["sf" + str(h)] = None
        if sm is None:
            r["state"] = "WARMUP"; r["lock"] = 0; r["persist"] = None
            continue
        held += 1
        cl = (r.get("pf12") or 0) > -.015 and (r.get("sf12") or 0) > -.015
        cs = (r.get("pf12") or 0) < .015 and (r.get("sf12") or 0) < .015
        if st == 0:
            if sm >= .60 and cl:
                st = 1; held = 0; opp = 0
            elif sm <= .40 and cs:
                st = -1; held = 0; opp = 0
        elif st == 1 and held >= 6:
            if sm <= .35 and cs:
                opp += 1
                if opp >= 2:
                    st = -1; held = 0; opp = 0
            else:
                opp = 0
                if sm < .46:
                    st = 0; held = 0
        elif st == -1 and held >= 6:
            if sm >= .65 and cl:
                opp += 1
                if opp >= 2:
                    st = 1; held = 0; opp = 0
            else:
                opp = 0
                if sm > .54:
                    st = 0; held = 0
        r["state"] = "LONG" if st == 1 else "SHORT" if st == -1 else "NEUTRAL"
        r["lock"] = max(0, 6 - held) if st else 0
        agree = total = 0
        dr = 1 if sm >= .5 else -1
        for h in [6, 12, 24]:
            for k in ["mom", "pf", "sf"]:
                v = r.get(k + str(h))
                if v is not None:
                    total += 1
                    agree += 1 if (1 if v > 0 else -1 if v < 0 else 0) == dr else 0
        r["persist"] = clip(30 + 50 * abs(sm - .5) * 2 + (20 * agree / total if total else 0), 0, 100)


def latest_signal(symbol: str) -> dict:
    rows = fetch_rows(symbol)
    if len(rows) < 200:
        raise RuntimeError(f"Недостаточно 2H данных для {symbol}: {len(rows)}")
    a = build_adaptive(rows, MEMORY_DAYS)
    r = a[-1]
    return {
        "symbol": symbol,
        "bar_ts": r["ts"],
        "bar_close_ts": r["closeTs"],
        "close": r["c"],
        "state": r.get("state", "WARMUP"),
        "p24": r.get("p24"),
        "persist": r.get("persist"),
        "pf12": r.get("pf12"),
        "sf12": r.get("sf12"),
        "mom12": r.get("mom12"),
        "rows": len(a),
    }

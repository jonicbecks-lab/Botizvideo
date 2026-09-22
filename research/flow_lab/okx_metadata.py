from __future__ import annotations

from typing import Iterable

import requests

OKX_REST = "https://openapi.okx.com"


def fetch_linear_swap_base_values(symbols: Iterable[str], timeout: float = 10.0) -> dict[str, float]:
    """Return base units represented by one OKX linear swap contract."""
    wanted = set(symbols)
    r = requests.get(f"{OKX_REST}/api/v5/public/instruments", params={"instType": "SWAP"}, timeout=timeout)
    r.raise_for_status()
    body = r.json()
    if body.get("code") not in {None, "0", 0}:
        raise RuntimeError(f"OKX instruments error: {body}")
    result = {}
    for row in body.get("data", []):
        inst = row.get("instId")
        if inst not in wanted:
            continue
        base = inst.split("-")[0]
        if row.get("ctValCcy") != base:
            raise ValueError(f"{inst} ctValCcy={row.get('ctValCcy')} is not base asset {base}")
        ct_val = float(row["ctVal"])
        ct_mult = float(row.get("ctMult") or 1.0)
        result[inst] = ct_val * ct_mult
    missing = wanted - result.keys()
    if missing:
        raise RuntimeError(f"missing OKX instrument metadata: {sorted(missing)}")
    return result

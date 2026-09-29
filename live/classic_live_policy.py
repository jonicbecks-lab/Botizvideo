from __future__ import annotations

import math
import statistics
from typing import Any

from . import research_server
from .engine import ACTIVE_STATUSES, now_iso, now_ms
from .live_ladder import MANUAL_DEPTHS, MANUAL_WEIGHTS, LadderLevel, round_perp_price


CLASSIC_LEVERAGE = 10
CLASSIC_LEVEL_COUNT = 8
_MAX_PLACEMENT_SPAN_MS = 15 * 60 * 1000
_PRICE_REL_TOLERANCE = 2e-6
_WEIGHT_ABS_TOLERANCE = 0.015
_INSTALLED = False

_ENGINE_CLASS = research_server._persistent.SafeCompatibleGalkaLiveEngine
_ORIGINAL_START = _ENGINE_CLASS.start
_ORIGINAL_LOAD_CONFIG = research_server._persistent.load_config


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _integer(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def detect_classic_orphan_ladder(
    orders: list[dict[str, Any]],
    sz_decimals: int,
) -> dict[str, Any] | None:
    """Recognize only an untouched eight-entry classic GALKA ladder.

    This is deliberately strict because a false positive would make GALKA claim
    unrelated real exchange orders. Detection never mutates the venue.
    """
    if len(orders) != CLASSIC_LEVEL_COUNT:
        return None

    rows: list[dict[str, Any]] = []
    seen_oids: set[int] = set()
    for raw in orders:
        oid = _integer(raw.get("oid"))
        price = _number(raw.get("price"))
        size = _number(raw.get("size"))
        original_size = _number(raw.get("originalSize"), size)
        side = str(raw.get("side") or "")
        if (
            oid <= 0
            or oid in seen_oids
            or price <= 0
            or size <= 0
            or original_size <= 0
            or side not in {"B", "Buy", "buy"}
            or bool(raw.get("reduceOnly"))
            or bool(raw.get("isTrigger"))
        ):
            return None
        size_tolerance = max(1e-12, 10 ** (-int(sz_decimals)) / 2)
        if abs(size - original_size) > size_tolerance:
            # A partially filled orphan needs fill-by-fill recovery, not adoption.
            return None
        seen_oids.add(oid)
        row = dict(raw)
        row["oid"] = oid
        row["price"] = price
        row["size"] = size
        row["originalSize"] = original_size
        rows.append(row)

    rows.sort(key=lambda row: float(row["price"]), reverse=True)
    timestamps = [_integer(row.get("timestamp")) for row in rows if _integer(row.get("timestamp")) > 0]
    if timestamps and max(timestamps) - min(timestamps) > _MAX_PLACEMENT_SPAN_MS:
        return None

    inferred = [
        float(row["price"]) / (1.0 - depth / 100.0)
        for row, depth in zip(rows, MANUAL_DEPTHS)
    ]
    galka_price = float(statistics.median(inferred))
    if not math.isfinite(galka_price) or galka_price <= 0:
        return None

    for row, depth in zip(rows, MANUAL_DEPTHS):
        expected = round_perp_price(galka_price * (1.0 - depth / 100.0), sz_decimals)
        tolerance = max(1e-9, abs(expected) * _PRICE_REL_TOLERANCE)
        if abs(float(row["price"]) - expected) > tolerance:
            return None

    notionals = [float(row["price"]) * float(row["originalSize"]) for row in rows]
    total_notional = sum(notionals)
    if total_notional <= 0:
        return None
    normalized = [value / total_notional for value in notionals]
    for actual, expected in zip(normalized, MANUAL_WEIGHTS):
        if abs(actual - expected) > _WEIGHT_ABS_TOLERANCE:
            return None

    return {
        "galkaPrice": galka_price,
        "orders": rows,
        "notionals": notionals,
        "totalNotional": total_notional,
        "weights": normalized,
    }


def _classic_load_config(*args: Any, **kwargs: Any):
    config = _ORIGINAL_LOAD_CONFIG(*args, **kwargs)
    object.__setattr__(config, "leverage", CLASSIC_LEVERAGE)
    return config


def _clear_recovered_orphan_safe_mode(self: Any, recovered_coins: set[str]) -> None:
    system = self.state.setdefault("system", {})
    reason = str(system.get("safeModeReason") or "").strip()
    if not system.get("safeMode") or not reason:
        return
    parts = [part.strip() for part in reason.split(";") if part.strip()]
    allowed = {f"{coin}: {CLASSIC_LEVEL_COUNT} orphan orders" for coin in recovered_coins}
    if parts and all(part in allowed for part in parts):
        system["safeMode"] = False
        system["safeModeReason"] = None


def recover_flat_classic_orphans(self: Any) -> list[str]:
    """Adopt exact classic entry ladders without placing or cancelling orders.

    Adoption is allowed only when the venue is flat, there is no active local
    campaign for the coin, and all eight untouched entry orders exactly match the
    classic depth/weight signature. Anything ambiguous remains SAFE MODE.
    """
    account = self.gateway.fresh_account_state()
    all_orders = self.gateway.fresh_open_orders()
    recovered: list[str] = []

    for coin in ("BTC", "ETH", "BNB"):
        with self.lock:
            existing = self.state.get("campaigns", {}).get(coin)
            if existing and existing.get("status") in ACTIVE_STATUSES:
                continue

        actual_position = self._position_size(account, coin)
        if abs(actual_position) > self._size_tolerance(coin):
            continue

        coin_orders = [row for row in all_orders if row.get("coin") == coin]
        match = detect_classic_orphan_ladder(coin_orders, self.gateway.sz_decimals(coin))
        if match is None:
            continue

        rows = match["orders"]
        notionals = match["notionals"]
        galka_price = float(match["galkaPrice"])
        total_notional = float(match["totalNotional"])
        levels = [
            LadderLevel(
                index=index,
                depth_pct=float(depth),
                weight=float(weight),
                price=float(row["price"]),
                size=float(row["originalSize"]),
                notional=float(notional),
            )
            for index, (depth, weight, row, notional) in enumerate(
                zip(MANUAL_DEPTHS, MANUAL_WEIGHTS, rows, notionals),
                start=1,
            )
        ]
        campaign_id = f"RECOVER-{coin}-{now_ms()}"
        preview = {
            "requestedNotional": total_notional,
            "actualNotional": total_notional,
            "requiredMargin": total_notional / max(1, int(self.config.leverage)),
            "targetMargin": total_notional / max(1, int(self.config.leverage)),
            "sizingPolicy": "recovered_classic_orphan_v1",
        }
        campaign = self._new_campaign(campaign_id, coin, galka_price, preview, levels)

        timestamps = [_integer(row.get("timestamp")) for row in rows if _integer(row.get("timestamp")) > 0]
        created_ms = min(timestamps) if timestamps else now_ms()
        campaign.update(
            {
                "status": "waiting",
                "galkaPrice": galka_price,
                "createdMs": created_ms,
                "updatedAt": now_iso(),
                "leverage": CLASSIC_LEVERAGE,
                "requestedNotional": total_notional,
                "actualNotional": total_notional,
                "managedNetSize": 0.0,
                "actualPositionSize": 0.0,
                "hadPosition": False,
                "fillCursorMs": max(0, created_ms - 60_000),
                "recoveredOrphanOrders": True,
                "recoveredAt": now_iso(),
                "sizingPolicy": "recovered_classic_orphan_v1",
            }
        )
        campaign["entryOidMap"] = {}
        campaign["targetOidMap"] = {}
        campaign["entryCloidMap"] = {}
        campaign["targetCloidMap"] = {}
        for level_state, row in zip(campaign["levels"], rows):
            index = int(level_state["index"])
            cloid = str(row.get("cloid") or "").strip() or None
            level_state.update(
                {
                    "oid": int(row["oid"]),
                    "tpOid": None,
                    "entryCloid": cloid,
                    "targetCloid": None,
                    "status": "resting",
                    "filledSize": 0.0,
                    "averageFillPrice": 0.0,
                }
            )
            campaign["entryOidMap"][str(row["oid"])] = index
            if cloid:
                campaign["entryCloidMap"][cloid] = index

        with self.lock:
            self.state.setdefault("campaigns", {})[coin] = campaign
            self._event_locked(
                "live",
                f"{coin}: восстановлена классическая GALKA из 8 существующих биржевых лимиток; новые ордера не создавались",
                campaignId=campaign_id,
                recovered=True,
            )
            recovered.append(coin)
            _clear_recovered_orphan_safe_mode(self, set(recovered))
            self._save_locked()

    if recovered:
        with self.lock:
            _clear_recovered_orphan_safe_mode(self, set(recovered))
            self._save_locked()
    return recovered


def _start_with_classic_recovery(self: Any) -> None:
    try:
        with self.action_lock:
            recover_flat_classic_orphans(self)
    except Exception as exc:
        # Do not weaken fail-closed startup: normal reconciliation runs next and
        # will keep/enable SAFE MODE if anything remains unexplained.
        with self.lock:
            self._event_locked(
                "error",
                f"Автовосстановление классической orphan GALKA не выполнено: {type(exc).__name__}",
            )
            self._save_locked()
    _ORIGINAL_START(self)


def install() -> None:
    """Install the fixed production classic policy for the Dev Control runtime."""
    global _INSTALLED
    if _INSTALLED:
        return
    research_server._persistent.load_config = _classic_load_config
    _ENGINE_CLASS.start = _start_with_classic_recovery
    _INSTALLED = True

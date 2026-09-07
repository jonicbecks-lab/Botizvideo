from __future__ import annotations

import math
import time
import uuid
from copy import deepcopy
from typing import Any

from .engine import LiveEngineError
from .hyperliquid_gateway import GatewayError, PlacedOrder, _PENDING_ORDER_STATUSES
from .live_ladder import round_perp_price, round_size_down

ACTIVATE_CONFIRMATION = "ACTIVATE_GALKA_MEM_BREAK_EVEN"
MOVE_PREFIX = "MOVE_GALKA_MEM_PROTECTION:"
PROTECTION_KIND = "break_even_stop_market"


def _new_cloid() -> str:
    return "0x" + uuid.uuid4().hex


def install_gateway_protection(base_gateway_cls):
    if getattr(base_gateway_cls, "__galka_mem_protection__", False):
        return base_gateway_cls

    class ProtectedMemGateway(base_gateway_cls):
        __galka_mem_protection__ = True

        def place_or_replace_protection(
            self,
            coin: str,
            quantity: float,
            trigger_price: float,
            *,
            existing_oid: int | None = None,
            cloid: str | None = None,
        ) -> PlacedOrder:
            self._require_live_write("place or replace GALKA MEM protection stop")
            coin = self._coin(coin)
            quantity = round_size_down(abs(float(quantity)), self.sz_decimals(coin))
            trigger = round_perp_price(float(trigger_price), self.sz_decimals(coin))
            if quantity <= 0:
                raise GatewayError("Protection size rounded to zero")
            if not math.isfinite(trigger) or trigger <= 0:
                raise GatewayError("Protection trigger must be positive and finite")

            order_type = {
                "trigger": {
                    "triggerPx": trigger,
                    "isMarket": True,
                    "tpsl": "sl",
                }
            }
            with self._io_lock:
                if existing_oid:
                    response = self.exchange.modify_order(
                        int(existing_oid),
                        coin,
                        False,
                        quantity,
                        trigger,
                        order_type,
                        reduce_only=True,
                        cloid=self._cloid(cloid),
                    )
                else:
                    response = self.exchange.order(
                        coin,
                        False,
                        quantity,
                        trigger,
                        order_type,
                        reduce_only=True,
                        cloid=self._cloid(cloid),
                    )

            self._invalidate("open_orders", "account_state", "mem_account_state")
            rows = self._parse_order_response(response, [None], [cloid])
            if len(rows) != 1:
                raise GatewayError("Unexpected protection-order response")
            order = rows[0]

            if order.status in _PENDING_ORDER_STATUSES or order.oid <= 0:
                refreshed = self.fresh_open_orders(coin)
                matching = next(
                    (row for row in refreshed if row.get("cloid") == cloid and row.get("isTrigger")),
                    None,
                )
                if matching is None:
                    raise GatewayError(
                        f"Protection stop was not visible as an active trigger order: {order.status}"
                    )
                return PlacedOrder(
                    oid=int(matching["oid"]),
                    status="resting",
                    price=float(matching.get("triggerPrice") or trigger),
                    size=float(matching.get("size") or quantity),
                    cloid=cloid,
                )

            return PlacedOrder(
                oid=order.oid,
                status=order.status,
                price=trigger,
                size=quantity,
                cloid=cloid,
            )

    ProtectedMemGateway.__name__ = base_gateway_cls.__name__
    ProtectedMemGateway.__qualname__ = base_gateway_cls.__qualname__
    return ProtectedMemGateway


def install_engine_protection(base_engine_cls):
    if getattr(base_engine_cls, "__galka_mem_protection__", False):
        return base_engine_cls

    class ProtectedMemEngine(base_engine_cls):
        __galka_mem_protection__ = True

        def __init__(self, *args, **kwargs):
            self._last_protection_fill_size = 0.0
            super().__init__(*args, **kwargs)
            with self.lock:
                campaign = self.state.get("campaign")
                if campaign:
                    self._ensure_protection_locked(campaign)
                    self._save_locked()

        def _ensure_protection_locked(self, campaign: dict[str, Any]) -> dict[str, Any]:
            protection = campaign.get("protection")
            if not isinstance(protection, dict):
                protection = {
                    "kind": PROTECTION_KIND,
                    "enabled": False,
                    "cloid": _new_cloid(),
                    "oid": None,
                    "triggerPrice": None,
                    "size": 0.0,
                    "filledSize": 0.0,
                    "updatedAt": None,
                }
                campaign["protection"] = protection
            else:
                protection.setdefault("kind", PROTECTION_KIND)
                protection.setdefault("enabled", False)
                protection.setdefault("cloid", _new_cloid())
                protection.setdefault("oid", None)
                protection.setdefault("triggerPrice", None)
                protection.setdefault("size", 0.0)
                protection.setdefault("filledSize", 0.0)
                protection.setdefault("updatedAt", None)
            return protection

        def create_campaign(self, *args, **kwargs):
            super().create_campaign(*args, **kwargs)
            with self.lock:
                campaign = self.state.get("campaign")
                if campaign:
                    self._ensure_protection_locked(campaign)
                    self._save_locked()
                    return deepcopy(campaign)
            return None

        def _owned_cloids(self, campaign: dict[str, Any]) -> set[str]:
            output = set(super()._owned_cloids(campaign))
            with self.lock:
                protection = self._ensure_protection_locked(campaign)
                if protection.get("cloid"):
                    output.add(str(protection["cloid"]))
            return output

        def _aggregate_fills(self, fills: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
            output = base_engine_cls._aggregate_fills(fills)
            with self.lock:
                campaign = self.state.get("campaign")
                if not campaign:
                    self._last_protection_fill_size = 0.0
                    return output
                protection = self._ensure_protection_locked(campaign)
                protection_cloid = str(protection.get("cloid") or "")
                aggregate = output.pop(protection_cloid, None)

            if not aggregate or float(aggregate.get("size") or 0.0) <= 0:
                self._last_protection_fill_size = 0.0
                return output

            protection_size = float(aggregate["size"])
            protection_notional = float(aggregate.get("notional") or 0.0)
            protection_avg = protection_notional / protection_size if protection_size > 0 else 0.0
            self._last_protection_fill_size = protection_size

            remaining = protection_size
            for basket in ("upper", "lower"):
                filled = sum(
                    float(output.get(row["entryCloid"], {}).get("size") or 0.0)
                    for row in campaign.get("levels", [])
                    if row.get("basket") == basket
                )
                target = campaign.get("targets", {}).get(basket, {})
                target_cloid = str(target.get("cloid") or "")
                target_agg = output.setdefault(target_cloid, {"size": 0.0, "notional": 0.0})
                already_closed = float(target_agg.get("size") or 0.0)
                logical_open = max(0.0, filled - already_closed)
                allocated = min(remaining, logical_open)
                if allocated > 0:
                    target_agg["size"] = already_closed + allocated
                    target_agg["notional"] = float(target_agg.get("notional") or 0.0) + protection_avg * allocated
                    remaining -= allocated
                if remaining <= 1e-12:
                    break

            if remaining > 1e-12:
                upper = campaign.get("targets", {}).get("upper", {})
                upper_cloid = str(upper.get("cloid") or "")
                target_agg = output.setdefault(upper_cloid, {"size": 0.0, "notional": 0.0})
                target_agg["size"] = float(target_agg.get("size") or 0.0) + remaining
                target_agg["notional"] = float(target_agg.get("notional") or 0.0) + protection_avg * remaining

            with self.lock:
                protection = self._ensure_protection_locked(campaign)
                protection["filledSize"] = protection_size
            return output

        def _finish_campaign(self, campaign: dict[str, Any], reason: str, status: str = "completed") -> None:
            if self._last_protection_fill_size > 0 and status == "completed":
                reason = "manual break-even/profit protection stop; one-shot GALKA retired"
            return super()._finish_campaign(campaign, reason, status=status)

        def _break_even_floor(self, account: dict[str, Any], coin: str) -> float:
            row = account.get("positions", {}).get(coin) or {}
            entry = float(row.get("entryPrice") or 0.0)
            if entry <= 0:
                return 0.0
            maker = max(0.0, float(getattr(self.config, "maker_fee_rate", 0.00015)))
            taker = max(0.0, float(getattr(self.config, "taker_fee_rate", 0.00045)))
            return round_perp_price(entry * (1.0 + maker + taker), self.gateway.sz_decimals(coin))

        def _set_protection(self, trigger_price: float | None) -> dict[str, Any]:
            self._require_live()
            with self.action_lock:
                with self.lock:
                    if self.state.get("system", {}).get("safeMode"):
                        raise LiveEngineError(
                            f"GALKA MEM SAFE MODE: {self.state['system'].get('safeModeReason') or 'reconcile first'}"
                        )
                    campaign = self._active_campaign_locked()
                    if not campaign:
                        raise LiveEngineError("No active GALKA MEM campaign")
                    protection = self._ensure_protection_locked(campaign)
                    coin = campaign["coin"]

                account = self.gateway.fresh_account_state()
                position_row = account.get("positions", {}).get(coin) or {}
                position = float(position_row.get("size") or 0.0)
                tolerance = self._size_tolerance(coin)
                if position <= tolerance:
                    raise LiveEngineError("Break-even protection requires an open long position")

                floor = self._break_even_floor(account, coin)
                if floor <= 0:
                    raise LiveEngineError("Cannot calculate break-even price from the live position")

                mid = float(self.gateway.mids().get(coin) or 0.0)
                requested = floor if trigger_price is None else float(trigger_price)
                if not math.isfinite(requested) or requested <= 0:
                    raise LiveEngineError("Protection price must be positive and finite")
                requested = round_perp_price(requested, self.gateway.sz_decimals(coin))

                if requested + max(1e-12, floor * 1e-10) < floor:
                    raise LiveEngineError(f"Protection cannot be below break-even {floor:g}")
                current = float(protection.get("triggerPrice") or 0.0)
                if protection.get("enabled") and current > 0 and requested + max(1e-12, current * 1e-10) < current:
                    raise LiveEngineError(f"Protection can only move upward from {current:g}")
                if mid <= 0 or requested >= mid:
                    raise LiveEngineError(
                        f"Protection {requested:g} must remain below current market {mid:g}"
                    )

                open_orders = self.gateway.fresh_open_orders(coin)
                matching = next(
                    (
                        row for row in open_orders
                        if row.get("cloid") == protection.get("cloid") and row.get("isTrigger")
                    ),
                    None,
                )
                order = self.gateway.place_or_replace_protection(
                    coin,
                    position,
                    requested,
                    existing_oid=int(matching["oid"]) if matching else None,
                    cloid=str(protection["cloid"]),
                )
                with self.lock:
                    protection = self._ensure_protection_locked(campaign)
                    protection.update(
                        {
                            "enabled": True,
                            "oid": order.oid,
                            "triggerPrice": requested,
                            "size": position,
                            "filledSize": 0.0,
                            "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        }
                    )
                    self._event_locked(
                        "ok",
                        f"GALKA MEM {coin}: protection stop set at {requested:g}",
                        breakEven=floor,
                    )
                    self._save_locked()
                    return deepcopy(protection)

        def reconcile(self, confirmation: str) -> dict[str, Any]:
            if confirmation == ACTIVATE_CONFIRMATION:
                self._set_protection(None)
                return self.status()
            if confirmation.startswith(MOVE_PREFIX):
                try:
                    price = float(confirmation[len(MOVE_PREFIX):])
                except (TypeError, ValueError) as exc:
                    raise LiveEngineError("Invalid protection price") from exc
                self._set_protection(price)
                return self.status()
            return super().reconcile(confirmation)

        def _sync_campaign(self) -> None:
            self._last_protection_fill_size = 0.0
            super()._sync_campaign()

            with self.lock:
                campaign = self._active_campaign_locked()
                if not campaign:
                    return
                protection = self._ensure_protection_locked(campaign)
                if not protection.get("enabled"):
                    return
                coin = campaign["coin"]
                trigger = float(protection.get("triggerPrice") or 0.0)
                protection_cloid = str(protection.get("cloid") or "")

            if self._last_protection_fill_size > 0:
                account = self.gateway.fresh_account_state()
                residual = self._position_size(account, coin)
                if residual > self._size_tolerance(coin):
                    self._cancel_entry_orders(campaign, self.gateway.fresh_open_orders(coin))
                    with self.lock:
                        campaign["status"] = "recovery"
                        self._set_safe_mode_locked(
                            f"{coin} protection stop partially filled; residual position requires review"
                        )
                        self._event_locked("risk", f"{coin} protection stop left a residual position")
                        self._save_locked()
                return

            if trigger <= 0:
                return
            account = self.gateway.fresh_account_state()
            position = self._position_size(account, coin)
            tolerance = self._size_tolerance(coin)
            if position <= tolerance:
                return

            open_orders = self.gateway.fresh_open_orders(coin)
            matching = next(
                (
                    row for row in open_orders
                    if row.get("cloid") == protection_cloid and row.get("isTrigger")
                ),
                None,
            )
            same_size = matching and abs(float(matching.get("size") or 0.0) - position) <= tolerance
            same_trigger = matching and abs(float(matching.get("triggerPrice") or 0.0) - trigger) <= max(
                1e-12, trigger * 1e-8
            )
            if matching and same_size and same_trigger:
                return

            mid = float(self.gateway.mids().get(coin) or 0.0)
            if mid <= trigger:
                return
            order = self.gateway.place_or_replace_protection(
                coin,
                position,
                trigger,
                existing_oid=int(matching["oid"]) if matching else None,
                cloid=protection_cloid,
            )
            with self.lock:
                protection = self._ensure_protection_locked(campaign)
                protection["oid"] = order.oid
                protection["size"] = position
                protection["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                self._save_locked()

        def status(self) -> dict[str, Any]:
            payload = super().status()
            campaign = payload.get("campaign")
            if not campaign:
                return payload
            protection = campaign.get("protection")
            if not isinstance(protection, dict):
                protection = {
                    "kind": PROTECTION_KIND,
                    "enabled": False,
                    "triggerPrice": None,
                    "size": 0.0,
                    "filledSize": 0.0,
                }
                campaign["protection"] = protection

            coin = campaign.get("coin")
            account = payload.get("accountState") or {}
            position_row = (account.get("positions") or {}).get(coin) or {}
            position = float(position_row.get("size") or 0.0)
            floor = self._break_even_floor(account, coin) if coin else 0.0
            mid = float((payload.get("mids") or {}).get(coin) or 0.0) if coin else 0.0
            protection["breakEvenPrice"] = floor or None
            protection["currentMarket"] = mid or None
            protection["canActivate"] = bool(
                coin and position > self._size_tolerance(coin) and floor > 0 and mid > floor
            )
            protection["canMoveUp"] = bool(
                protection.get("enabled") and mid > float(protection.get("triggerPrice") or 0.0)
            )
            return payload

    ProtectedMemEngine.__name__ = base_engine_cls.__name__
    ProtectedMemEngine.__qualname__ = base_engine_cls.__qualname__
    return ProtectedMemEngine

from __future__ import annotations

import json
import math
import os
import threading
import time
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

from .config import LiveConfig
from .engine import LiveEngineError
from .live_ladder import round_perp_price
from .mem_gateway import MemHyperliquidGateway
from .mem_ladder import build_mem_plan

ACTIVE_STATUSES = {"placing", "waiting", "open", "closing", "recovery"}
TERMINAL_STATUSES = {"completed", "cancelled", "failed", "emergency_closed"}


def now_ms() -> int:
    return int(time.time() * 1000)


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def new_cloid() -> str:
    return "0x" + uuid.uuid4().hex


class GalkaMemEngine:
    """One-shot GALKA MEM campaign coordinator.

    A campaign gets one set of entry orders. If the upper basket completes before
    any lower fill, every remaining entry is cancelled and the GALKA is finished.
    If a lower entry fills, the campaign becomes the big cycle: lower inventory
    exits at GALKA and upper inventory exits at its own weighted-average ROE TP.
    No entry level is ever re-armed.
    """

    def __init__(self, config: LiveConfig, gateway: MemHyperliquidGateway):
        self.config = config
        self.gateway = gateway
        self.state_path = config.data_dir / "mem-state.json"
        self.lock = threading.RLock()
        self.action_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.state = self._load_state()
        self.monitor_thread = threading.Thread(
            target=self._monitor_loop,
            name="galka-mem-monitor",
            daemon=True,
        )

    @staticmethod
    def _empty_state() -> dict[str, Any]:
        return {
            "version": 1,
            "system": {
                "safeMode": False,
                "safeModeReason": None,
                "monitorHeartbeatAt": None,
                "lastSyncAt": None,
            },
            "campaign": None,
            "events": [],
        }

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return self._empty_state()
        try:
            if self.state_path.is_symlink() or self.state_path.stat().st_size > 5_000_000:
                raise ValueError("unsafe mem state")
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("version") != 1:
                raise ValueError("unsupported mem state")
            data.setdefault("system", {})
            data.setdefault("campaign", None)
            data.setdefault("events", [])
            return data
        except Exception:
            state = self._empty_state()
            state["system"]["safeMode"] = True
            state["system"]["safeModeReason"] = "GALKA MEM state cannot be trusted"
            return state

    def _save_locked(self) -> None:
        payload = json.dumps(self.state, ensure_ascii=False, indent=2, allow_nan=False)
        tmp = self.state_path.with_name(f".{self.state_path.name}.{uuid.uuid4().hex}.tmp")
        descriptor = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                descriptor = -1
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.state_path)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass

    def _event_locked(self, event_type: str, message: str, **meta: Any) -> None:
        events = self.state.setdefault("events", [])
        events.append({"time": now_iso(), "type": event_type, "message": message, "meta": meta})
        del events[:-300]

    def _set_safe_mode_locked(self, reason: str) -> None:
        self.state["system"]["safeMode"] = True
        self.state["system"]["safeModeReason"] = reason

    def _clear_safe_mode_locked(self) -> None:
        self.state["system"]["safeMode"] = False
        self.state["system"]["safeModeReason"] = None

    def start(self) -> None:
        campaign = self.state.get("campaign")
        if campaign and campaign.get("status") in ACTIVE_STATUSES:
            try:
                with self.action_lock:
                    self._sync_campaign()
            except Exception as exc:
                with self.lock:
                    self._set_safe_mode_locked(f"Startup MEM reconciliation failed: {exc}")
                    self._event_locked("risk", "GALKA MEM startup reconciliation failed")
                    self._save_locked()
        self.monitor_thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.monitor_thread.is_alive():
            self.monitor_thread.join(timeout=3)

    def _require_live(self) -> None:
        if not self.config.live_enabled:
            raise LiveEngineError("LIVE is disabled in the private Hyperliquid config")

    def _active_campaign_locked(self) -> dict[str, Any] | None:
        campaign = self.state.get("campaign")
        if campaign and campaign.get("status") in ACTIVE_STATUSES:
            return campaign
        return None

    def _size_tolerance(self, coin: str) -> float:
        return 10 ** (-self.gateway.sz_decimals(coin)) * 1.01

    @staticmethod
    def _position_size(account: dict[str, Any], coin: str) -> float:
        row = account.get("positions", {}).get(coin)
        return float(row.get("size") or 0.0) if row else 0.0

    def preview(
        self,
        coin: str,
        galka_price: float,
        upper_prices: list[float],
        campaign_margin: float,
        leverage: int,
    ) -> dict[str, Any]:
        coin = self.gateway._coin(coin)
        galka_price = float(galka_price)
        campaign_margin = float(campaign_margin)
        leverage = int(leverage)
        if not math.isfinite(galka_price) or galka_price <= 0:
            raise LiveEngineError("GALKA price must be a positive finite number")
        if not all(math.isfinite(float(value)) for value in upper_prices):
            raise LiveEngineError("Upper prices must be finite numbers")

        mids = self.gateway.mids()
        mid = mids.get(coin)
        if not mid:
            raise LiveEngineError(f"No current Hyperliquid price for {coin}")
        maximum = self.gateway.max_leverage(coin)
        plan = build_mem_plan(
            galka_price=galka_price,
            upper_prices=upper_prices,
            campaign_margin=campaign_margin,
            leverage=leverage,
            max_leverage=maximum,
            sz_decimals=self.gateway.sz_decimals(coin),
        )
        highest = max(level["price"] for level in plan["levels"])
        plan["currentPrice"] = mid
        plan["coin"] = coin
        plan["marketMaxLeverage"] = maximum
        plan["entriesRestBelowMarket"] = highest < mid
        if highest >= mid:
            plan["safe"] = False
            plan["marketReason"] = (
                f"Highest buy limit {highest:g} is not below current market {mid:g}; "
                "ALO would not safely rest"
            )
        account = self.gateway.fresh_account_state()
        plan["accountValue"] = account["accountValue"]
        plan["withdrawable"] = account["withdrawable"]
        plan["allowedCampaignMargin"] = max(0.0, account["withdrawable"] * self.config.max_margin_fraction)
        plan["marginFractionSafe"] = campaign_margin <= plan["allowedCampaignMargin"] + 1e-9
        if not plan["marginFractionSafe"]:
            plan["safe"] = False
            plan["marginReason"] = (
                f"Campaign margin ${campaign_margin:.2f} exceeds configured wallet guard "
                f"${plan['allowedCampaignMargin']:.2f}"
            )
        return plan

    def create_campaign(
        self,
        coin: str,
        galka_price: float,
        upper_prices: list[float],
        campaign_margin: float,
        leverage: int,
        confirmation: str,
    ) -> dict[str, Any]:
        self._require_live()
        if confirmation != "PLACE_GALKA_MEM_REAL_ORDERS":
            raise LiveEngineError("Real GALKA MEM order confirmation is missing")

        with self.action_lock:
            with self.lock:
                if self.state["system"].get("safeMode"):
                    raise LiveEngineError(
                        f"GALKA MEM SAFE MODE: {self.state['system'].get('safeModeReason') or 'reconcile first'}"
                    )
                if self._active_campaign_locked():
                    raise LiveEngineError("A GALKA MEM campaign is already active")

            preview = self.preview(coin, galka_price, upper_prices, campaign_margin, leverage)
            if not preview.get("safe"):
                reasons = [
                    preview.get("marketReason"),
                    preview.get("marginReason"),
                ]
                failed = [
                    row for row in preview.get("liquidationStates", []) if not row.get("safe")
                ]
                if failed:
                    row = failed[0]
                    reasons.append(
                        f"liquidation {row['estimated_liquidation_price']:.8g} after L{row['last_level_index']} "
                        f"must be <= {row['required_max_liquidation_price']:.8g}"
                    )
                raise LiveEngineError("; ".join(reason for reason in reasons if reason) or "Safety gate failed")

            coin = preview["coin"]
            account = self.gateway.fresh_account_state()
            position = self._position_size(account, coin)
            tolerance = self._size_tolerance(coin)
            if abs(position) > tolerance:
                raise LiveEngineError(f"{coin} already has a real position: {position:g}")
            coin_orders = self.gateway.fresh_open_orders(coin)
            if coin_orders:
                raise LiveEngineError(f"{coin} already has {len(coin_orders)} open orders")

            created_ms = now_ms()
            campaign_id = uuid.uuid4().hex
            levels = []
            for source in preview["levels"]:
                row = dict(source)
                row.update(
                    {
                        "entryCloid": new_cloid(),
                        "entryOid": None,
                        "status": "planned",
                        "filledSize": 0.0,
                        "fillNotional": 0.0,
                        "averageFillPrice": 0.0,
                    }
                )
                levels.append(row)
            campaign = {
                "id": campaign_id,
                "coin": coin,
                "createdAt": now_iso(),
                "createdMs": created_ms,
                "status": "placing",
                "cycle": "small",
                "galkaPrice": preview["galkaPrice"],
                "marginBudget": float(campaign_margin),
                "leverage": int(leverage),
                "maxLeverage": int(preview["maxLeverage"]),
                "targetRoe": float(preview["targetRoe"]),
                "requiredMaxLiquidationPrice": float(preview["requiredMaxLiquidationPrice"]),
                "levels": levels,
                "lowerTouched": False,
                "targets": {
                    "upper": {"cloid": new_cloid(), "oid": None, "price": None, "size": 0.0},
                    "lower": {"cloid": new_cloid(), "oid": None, "price": preview["lowerTakeProfit"], "size": 0.0},
                },
                "completedReason": None,
            }
            with self.lock:
                self.state["campaign"] = campaign
                self._event_locked("info", f"GALKA MEM {coin} campaign created; placing entry ladder")
                self._save_locked()

            placed_oids: list[int] = []
            try:
                self.gateway.set_leverage_value(coin, leverage)
                for row in levels:
                    order = self.gateway.place_limit_order(
                        coin=coin,
                        is_buy=True,
                        size=row["size"],
                        price=row["price"],
                        reduce_only=False,
                        tif="Alo",
                        cloid=row["entryCloid"],
                    )
                    if order.status != "resting" or order.oid <= 0:
                        raise LiveEngineError(
                            f"L{row['index']} did not become a resting ALO order: {order.status}"
                        )
                    placed_oids.append(order.oid)
                    with self.lock:
                        row["entryOid"] = order.oid
                        row["status"] = "resting"
                        self._save_locked()
            except Exception as exc:
                try:
                    self.gateway.cancel_oids(coin, placed_oids)
                except Exception:
                    pass
                actual = self._position_size(self.gateway.fresh_account_state(), coin)
                with self.lock:
                    campaign["status"] = "failed" if abs(actual) <= tolerance else "recovery"
                    campaign["completedReason"] = f"entry placement failed: {type(exc).__name__}"
                    if campaign["status"] == "recovery":
                        self._set_safe_mode_locked("Entry placement failed after a real fill; manual review required")
                    self._event_locked("error", f"GALKA MEM entry placement failed: {exc}")
                    self._save_locked()
                raise

            with self.lock:
                campaign["status"] = "waiting"
                self._event_locked("ok", f"GALKA MEM {coin}: all one-shot entry limits are resting")
                self._save_locked()
            return deepcopy(campaign)

    def _owned_cloids(self, campaign: dict[str, Any]) -> set[str]:
        output = {row["entryCloid"] for row in campaign.get("levels", []) if row.get("entryCloid")}
        for target in campaign.get("targets", {}).values():
            if target.get("cloid"):
                output.add(target["cloid"])
        return output

    def _cancel_entry_orders(self, campaign: dict[str, Any], open_orders: list[dict[str, Any]]) -> None:
        entry_cloids = {row["entryCloid"] for row in campaign["levels"]}
        oids = [
            int(row["oid"])
            for row in open_orders
            if row.get("cloid") in entry_cloids and not row.get("reduceOnly")
        ]
        if oids:
            self.gateway.cancel_oids(campaign["coin"], oids)

    def _cancel_all_owned_orders(self, campaign: dict[str, Any], open_orders: list[dict[str, Any]]) -> None:
        owned = self._owned_cloids(campaign)
        oids = [int(row["oid"]) for row in open_orders if row.get("cloid") in owned]
        if oids:
            self.gateway.cancel_oids(campaign["coin"], oids)

    @staticmethod
    def _aggregate_fills(fills: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
        output: dict[str, dict[str, float]] = {}
        for fill in fills:
            cloid = fill.get("cloid")
            if not cloid:
                continue
            row = output.setdefault(str(cloid), {"size": 0.0, "notional": 0.0})
            size = abs(float(fill.get("size") or 0.0))
            price = float(fill.get("price") or 0.0)
            row["size"] += size
            row["notional"] += size * price
        return output

    def _logical_open_size(self, campaign: dict[str, Any], basket: str) -> float:
        filled = sum(
            float(row.get("filledSize") or 0.0)
            for row in campaign["levels"]
            if row["basket"] == basket
        )
        target = campaign["targets"][basket]
        closed = float(target.get("filledSize") or 0.0)
        return max(0.0, filled - closed)

    def _basket_fill_average(self, campaign: dict[str, Any], basket: str) -> float:
        size = 0.0
        notional = 0.0
        for row in campaign["levels"]:
            if row["basket"] != basket:
                continue
            size += float(row.get("filledSize") or 0.0)
            notional += float(row.get("fillNotional") or 0.0)
        return notional / size if size > 0 else 0.0

    def _ensure_target(
        self,
        campaign: dict[str, Any],
        basket: str,
        desired_size: float,
        desired_price: float,
        open_orders: list[dict[str, Any]],
    ) -> None:
        coin = campaign["coin"]
        tolerance = self._size_tolerance(coin)
        target = campaign["targets"][basket]
        matching = next(
            (row for row in open_orders if row.get("cloid") == target.get("cloid")),
            None,
        )
        if desired_size <= tolerance:
            if matching:
                self.gateway.cancel_oids(coin, [int(matching["oid"])])
            target["oid"] = None
            target["size"] = 0.0
            target["price"] = desired_price
            return

        desired_price = round_perp_price(desired_price, self.gateway.sz_decimals(coin))
        if matching:
            same_size = abs(float(matching.get("size") or 0.0) - desired_size) <= tolerance
            same_price = abs(float(matching.get("price") or 0.0) - desired_price) <= max(
                1e-12, desired_price * 1e-8
            )
            if same_size and same_price:
                target["oid"] = int(matching["oid"])
                target["size"] = desired_size
                target["price"] = desired_price
                return
        order = self.gateway.place_or_replace_target(
            coin,
            desired_size,
            desired_price,
            existing_oid=int(matching["oid"]) if matching else None,
            cloid=target["cloid"],
        )
        target["oid"] = order.oid
        target["size"] = desired_size
        target["price"] = desired_price

    def _finish_campaign(self, campaign: dict[str, Any], reason: str, status: str = "completed") -> None:
        open_orders = self.gateway.fresh_open_orders(campaign["coin"])
        self._cancel_all_owned_orders(campaign, open_orders)
        with self.lock:
            campaign["status"] = status
            campaign["completedReason"] = reason
            self._event_locked("ok", f"GALKA MEM {campaign['coin']} finished: {reason}")
            self._save_locked()

    def _sync_campaign(self) -> None:
        with self.lock:
            campaign = self._active_campaign_locked()
            if not campaign:
                return
            campaign = self.state["campaign"]
        coin = campaign["coin"]
        tolerance = self._size_tolerance(coin)
        open_orders = self.gateway.fresh_open_orders(coin)
        fills = [
            row for row in self.gateway.fills_since(max(0, int(campaign["createdMs"]) - 2_000))
            if row.get("coin") == coin
        ]
        fill_map = self._aggregate_fills(fills)
        account = self.gateway.fresh_account_state()
        actual_position = self._position_size(account, coin)

        owned = self._owned_cloids(campaign)
        unknown_orders = [row for row in open_orders if row.get("cloid") not in owned]
        if unknown_orders:
            self._cancel_entry_orders(campaign, open_orders)
            with self.lock:
                campaign["status"] = "recovery"
                self._set_safe_mode_locked(f"Unknown {coin} order appeared during GALKA MEM")
                self._event_locked("risk", f"Unknown {coin} order detected; pending MEM entries cancelled")
                self._save_locked()
            return

        open_by_cloid = {row.get("cloid"): row for row in open_orders if row.get("cloid")}
        with self.lock:
            for row in campaign["levels"]:
                aggregate = fill_map.get(row["entryCloid"], {"size": 0.0, "notional": 0.0})
                row["filledSize"] = aggregate["size"]
                row["fillNotional"] = aggregate["notional"]
                row["averageFillPrice"] = (
                    aggregate["notional"] / aggregate["size"] if aggregate["size"] > 0 else 0.0
                )
                if aggregate["size"] >= float(row["size"]) - tolerance:
                    row["status"] = "filled"
                elif aggregate["size"] > tolerance:
                    row["status"] = "partial"
                elif row["entryCloid"] in open_by_cloid:
                    row["status"] = "resting"
                else:
                    row["status"] = "cancelled"

            for basket in ("upper", "lower"):
                target = campaign["targets"][basket]
                aggregate = fill_map.get(target["cloid"], {"size": 0.0, "notional": 0.0})
                target["filledSize"] = aggregate["size"]
                target["fillNotional"] = aggregate["notional"]

            lower_touched = any(
                row["basket"] == "lower" and float(row.get("filledSize") or 0.0) > tolerance
                for row in campaign["levels"]
            )
            if lower_touched and not campaign.get("lowerTouched"):
                campaign["lowerTouched"] = True
                campaign["cycle"] = "big"
                self._event_locked("info", f"GALKA MEM {coin}: lower basket touched; big cycle active")

            expected_position = sum(float(row.get("filledSize") or 0.0) for row in campaign["levels"])
            expected_position -= sum(
                float(campaign["targets"][basket].get("filledSize") or 0.0)
                for basket in ("upper", "lower")
            )
            campaign["expectedPosition"] = max(0.0, expected_position)
            campaign["actualPosition"] = actual_position
            self._save_locked()

        if actual_position < -tolerance or abs(actual_position - max(0.0, expected_position)) > tolerance * 2:
            # One immediate retry avoids false recovery on an exchange read/fill race.
            time.sleep(0.15)
            retry_account = self.gateway.fresh_account_state()
            retry_position = self._position_size(retry_account, coin)
            retry_fills = [
                row for row in self.gateway.fills_since(max(0, int(campaign["createdMs"]) - 2_000))
                if row.get("coin") == coin
            ]
            retry_map = self._aggregate_fills(retry_fills)
            retry_expected = sum(retry_map.get(row["entryCloid"], {}).get("size", 0.0) for row in campaign["levels"])
            retry_expected -= sum(
                retry_map.get(campaign["targets"][basket]["cloid"], {}).get("size", 0.0)
                for basket in ("upper", "lower")
            )
            if retry_position < -tolerance or abs(retry_position - max(0.0, retry_expected)) > tolerance * 2:
                self._cancel_entry_orders(campaign, self.gateway.fresh_open_orders(coin))
                with self.lock:
                    campaign["status"] = "recovery"
                    self._set_safe_mode_locked(
                        f"{coin} real position does not match GALKA MEM-owned fills"
                    )
                    self._event_locked("risk", f"{coin} position mismatch; pending MEM entries cancelled")
                    self._save_locked()
                return
            actual_position = retry_position

        if campaign.get("status") == "recovery":
            return

        upper_open = self._logical_open_size(campaign, "upper")
        lower_open = self._logical_open_size(campaign, "lower")
        upper_filled = sum(
            float(row.get("filledSize") or 0.0)
            for row in campaign["levels"]
            if row["basket"] == "upper"
        )

        upper_average = self._basket_fill_average(campaign, "upper")
        upper_tp = (
            upper_average * (1.0 + float(campaign["targetRoe"]) / int(campaign["leverage"]))
            if upper_average > 0
            else float(campaign["galkaPrice"])
        )
        open_orders = self.gateway.fresh_open_orders(coin)
        self._ensure_target(campaign, "upper", upper_open, upper_tp, open_orders)
        open_orders = self.gateway.fresh_open_orders(coin)
        self._ensure_target(
            campaign,
            "lower",
            lower_open,
            float(campaign["galkaPrice"]),
            open_orders,
        )

        with self.lock:
            campaign["upperAverageFill"] = upper_average
            campaign["upperTakeProfit"] = round_perp_price(
                upper_tp, self.gateway.sz_decimals(coin)
            ) if upper_average > 0 else None
            campaign["status"] = "open" if actual_position > tolerance else "waiting"
            self.state["system"]["lastSyncAt"] = now_iso()
            self._save_locked()

        # Small cycle: upper inventory has been closed before any lower fill.
        if (
            not campaign.get("lowerTouched")
            and upper_filled > tolerance
            and upper_open <= tolerance
            and actual_position <= tolerance
        ):
            self._finish_campaign(campaign, "small cycle TP; one-shot GALKA retired")
            return

        # Big cycle finishes only when every owned open inventory is flat.
        if campaign.get("lowerTouched") and actual_position <= tolerance and upper_open <= tolerance and lower_open <= tolerance:
            self._finish_campaign(campaign, "big cycle flat; one-shot GALKA retired")

    def _monitor_loop(self) -> None:
        while not self.stop_event.wait(1.0):
            with self.lock:
                self.state["system"]["monitorHeartbeatAt"] = now_iso()
                active = self._active_campaign_locked() is not None
            if not active:
                continue
            if self.state["system"].get("safeMode"):
                continue
            try:
                with self.action_lock:
                    self._sync_campaign()
            except Exception as exc:
                with self.lock:
                    campaign = self._active_campaign_locked()
                if campaign:
                    try:
                        self._cancel_entry_orders(campaign, self.gateway.fresh_open_orders(campaign["coin"]))
                    except Exception:
                        pass
                with self.lock:
                    self._set_safe_mode_locked(f"GALKA MEM monitor failed: {type(exc).__name__}: {exc}")
                    self._event_locked("error", "GALKA MEM monitor failed; pending entries cancelled if possible")
                    self._save_locked()

    def status(self) -> dict[str, Any]:
        account = self.gateway.account_state()
        mids = self.gateway.mids()
        with self.lock:
            campaign = deepcopy(self.state.get("campaign"))
            system = deepcopy(self.state.get("system", {}))
            events = deepcopy(self.state.get("events", [])[-100:])
        return {
            "network": self.config.network_name,
            "account": self.config.masked_address,
            "liveEnabled": self.config.live_enabled,
            "isolated": True,
            "campaignMarginDefault": 100.0,
            "accountState": account,
            "mids": mids,
            "markets": self.gateway.market_catalog(),
            "campaign": campaign,
            "system": system,
            "events": events,
        }

    def reconcile(self, confirmation: str) -> dict[str, Any]:
        if confirmation != "RECONCILE_GALKA_MEM":
            raise LiveEngineError("GALKA MEM reconcile confirmation is missing")
        with self.action_lock:
            with self.lock:
                campaign = self._active_campaign_locked()
                self._clear_safe_mode_locked()
                self._save_locked()
            if campaign:
                self._sync_campaign()
        return self.status()

    def cancel_waiting_campaign(self, confirmation: str) -> dict[str, Any]:
        self._require_live()
        if confirmation != "CANCEL_GALKA_MEM":
            raise LiveEngineError("GALKA MEM cancel confirmation is missing")
        with self.action_lock:
            with self.lock:
                campaign = self._active_campaign_locked()
                if not campaign:
                    raise LiveEngineError("No active GALKA MEM campaign")
            account = self.gateway.fresh_account_state()
            tolerance = self._size_tolerance(campaign["coin"])
            if abs(self._position_size(account, campaign["coin"])) > tolerance:
                raise LiveEngineError("Campaign already has a real position; use emergency close if needed")
            fills = self.gateway.fills_since(max(0, int(campaign["createdMs"]) - 2_000))
            entry_cloids = {row["entryCloid"] for row in campaign["levels"]}
            if any(row.get("cloid") in entry_cloids for row in fills):
                raise LiveEngineError("Campaign has already received an entry fill")
            self._finish_campaign(campaign, "cancelled before any fill", status="cancelled")
            return deepcopy(campaign)

    def emergency_close(self, confirmation: str) -> dict[str, Any]:
        self._require_live()
        if confirmation != "EMERGENCY_CLOSE_GALKA_MEM":
            raise LiveEngineError("Emergency close confirmation is missing")
        with self.action_lock:
            with self.lock:
                campaign = self._active_campaign_locked()
                if not campaign:
                    raise LiveEngineError("No active GALKA MEM campaign")
            open_orders = self.gateway.fresh_open_orders(campaign["coin"])
            self._cancel_all_owned_orders(campaign, open_orders)
            account = self.gateway.fresh_account_state()
            position = self._position_size(account, campaign["coin"])
            if abs(position) > self._size_tolerance(campaign["coin"]):
                self.gateway.emergency_market_close(campaign["coin"], cloid=new_cloid())
            with self.lock:
                campaign["status"] = "emergency_closed"
                campaign["completedReason"] = "manual emergency close"
                self._event_locked("risk", f"GALKA MEM {campaign['coin']} emergency closed")
                self._save_locked()
            return deepcopy(campaign)

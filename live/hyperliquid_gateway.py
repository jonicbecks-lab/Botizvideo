from __future__ import annotations

import math
import threading
import time
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable, TypeVar

from .config import LiveConfig
from .live_ladder import LadderLevel, build_ladder, round_perp_price, round_size_down

try:
    import eth_account
    from hyperliquid.exchange import Exchange
    from hyperliquid.info import Info
    from hyperliquid.utils import constants
    from hyperliquid.utils.types import Cloid
except ImportError as exc:  # pragma: no cover - shown to the Termux user
    raise RuntimeError(
        "Hyperliquid SDK is not installed. Run bash scripts/setup-galka-live.sh"
    ) from exc


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
_PENDING_ORDER_STATUSES = {"waitingForFill", "waitingForTrigger"}
T = TypeVar("T")


class GatewayError(RuntimeError):
    pass


def _finite_number(value: Any, label: str, default: float = 0.0) -> float:
    """Parse an exchange number and reject NaN/Infinity fail-closed."""
    raw = default if value is None or value == "" else value
    try:
        parsed = float(raw)
    except (TypeError, ValueError) as exc:
        raise GatewayError(f"Invalid numeric field {label}: {value!r}") from exc
    if not math.isfinite(parsed):
        raise GatewayError(f"Non-finite numeric field {label}: {value!r}")
    return parsed


def _integer(value: Any, label: str, default: int = 0) -> int:
    raw = default if value is None or value == "" else value
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise GatewayError(f"Invalid integer field {label}: {value!r}") from exc


def _parse_user_abstraction(value: Any) -> str:
    """Normalize Hyperliquid's userAbstraction response across API versions."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("abstraction", "mode", "type"):
            candidate = value.get(key)
            if isinstance(candidate, str):
                return candidate
    return "default"


def _unified_usdc_values(spot_state: dict[str, Any]) -> tuple[float, float]:
    account_value = 0.0
    available = 0.0
    for row in spot_state.get("balances", []):
        if str(row.get("coin") or "").upper() != "USDC":
            continue
        total = _finite_number(row.get("total"), "spot.USDC.total")
        hold = _finite_number(row.get("hold"), "spot.USDC.hold")
        account_value += total
        available += max(0.0, total - hold)
    return account_value, available


@dataclass(frozen=True)
class PlacedOrder:
    oid: int
    status: str
    level: int | None = None
    price: float | None = None
    size: float | None = None
    cloid: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "oid": self.oid,
            "status": self.status,
            "level": self.level,
            "price": self.price,
            "size": self.size,
            "cloid": self.cloid,
        }


@dataclass(frozen=True)
class EntryWithTarget:
    entry: PlacedOrder
    target: PlacedOrder


class HyperliquidGateway:
    """Strict, timeout-bounded adapter around the official Hyperliquid SDK."""

    def __init__(self, config: LiveConfig):
        self.config = config
        self._io_lock = threading.RLock()
        self._cache_lock = threading.RLock()
        self._cache: dict[str, tuple[float, Any]] = {}
        self._account_mode_value = "default"
        self._account_mode_checked_at = 0.0
        self.base_url = constants.MAINNET_API_URL if config.mainnet else constants.TESTNET_API_URL
        self.signer = eth_account.Account.from_key(config.api_secret_key)
        self.info = Info(self.base_url, skip_ws=True, timeout=config.request_timeout)
        self.exchange = Exchange(
            self.signer,
            self.base_url,
            account_address=config.account_address,
            timeout=config.request_timeout,
        )
        self._meta = self._read("meta", self.info.meta)
        self._universe = {item["name"]: item for item in self._meta["universe"]}

    def _cache_get(self, key: str, ttl: float) -> Any | None:
        with self._cache_lock:
            item = self._cache.get(key)
            if not item or time.monotonic() - item[0] > ttl:
                return None
            return deepcopy(item[1])

    def _cache_set(self, key: str, value: Any) -> Any:
        with self._cache_lock:
            self._cache[key] = (time.monotonic(), deepcopy(value))
        return value

    def _invalidate(self, *keys: str) -> None:
        with self._cache_lock:
            for key in keys:
                self._cache.pop(key, None)

    @property
    def agent_address(self) -> str:
        return self.signer.address.lower()

    def _require_live_write(self, action: str) -> None:
        """Block every exchange mutation unless the two-factor LIVE gate is enabled."""
        if not self.config.live_enabled:
            raise GatewayError(
                f"Hyperliquid write blocked ({action}): HL_LIVE_ENABLED is not enabled"
            )

    def _read(self, label: str, action: Callable[[], T], attempts: int = 2) -> T:
        last: Exception | None = None
        for index in range(attempts):
            try:
                with self._io_lock:
                    return action()
            except Exception as exc:  # network/SDK errors are normalized here
                last = exc
                if index + 1 < attempts:
                    time.sleep(0.20 * (index + 1))
        raise GatewayError(f"Hyperliquid read failed ({label}): {last}") from last

    def _coin(self, value: str) -> str:
        coin = value.upper().replace("USDT", "").replace("USD", "")
        if coin not in SUPPORTED_COINS:
            raise GatewayError(f"Unsupported coin: {value}")
        return coin

    @staticmethod
    def _cloid(value: str | None) -> Cloid | None:
        return Cloid.from_str(value) if value else None

    def sz_decimals(self, coin: str) -> int:
        coin = self._coin(coin)
        return int(self._universe[coin]["szDecimals"])

    def max_leverage(self, coin: str) -> int:
        coin = self._coin(coin)
        return int(self._universe[coin].get("maxLeverage", 1))

    def user_abstraction(self) -> str:
        # Account abstraction rarely changes. Caching avoids adding a separate
        # weighted info request to every position poll.
        if time.monotonic() - self._account_mode_checked_at < 300:
            return self._account_mode_value
        try:
            response = self._read(
                "userAbstraction",
                lambda: self.info.post(
                    "/info",
                    {"type": "userAbstraction", "user": self.config.account_address},
                ),
            )
        except GatewayError:
            self._account_mode_checked_at = time.monotonic() - 270.0
            return self._account_mode_value
        self._account_mode_value = _parse_user_abstraction(response)
        self._account_mode_checked_at = time.monotonic()
        return self._account_mode_value

    def account_state(self, fresh: bool = False) -> dict[str, Any]:
        cached = None if fresh else self._cache_get("account_state", 1.25)
        if cached is not None:
            return cached
        state = self._read("user_state", lambda: self.info.user_state(self.config.account_address))
        positions: dict[str, dict[str, Any]] = {}
        for row in state.get("assetPositions", []):
            position = row.get("position", {})
            coin = position.get("coin")
            if coin not in SUPPORTED_COINS:
                continue
            positions[coin] = {
                "coin": coin,
                "size": _finite_number(position.get("szi"), f"{coin}.position.szi"),
                "entryPrice": _finite_number(position.get("entryPx"), f"{coin}.position.entryPx"),
                "liquidationPrice": _finite_number(
                    position.get("liquidationPx"), f"{coin}.position.liquidationPx"
                ),
                "marginUsed": _finite_number(
                    position.get("marginUsed"), f"{coin}.position.marginUsed"
                ),
                "positionValue": _finite_number(
                    position.get("positionValue"), f"{coin}.position.positionValue"
                ),
                "unrealizedPnl": _finite_number(
                    position.get("unrealizedPnl"), f"{coin}.position.unrealizedPnl"
                ),
                "leverage": position.get("leverage") or {},
            }

        summary = state.get("marginSummary") or {}
        account_value = _finite_number(summary.get("accountValue"), "marginSummary.accountValue")
        withdrawable = _finite_number(state.get("withdrawable"), "withdrawable")
        account_mode = self.user_abstraction()
        if account_mode.lower() == "unifiedaccount":
            spot_state = self._read(
                "spot_user_state",
                lambda: self.info.spot_user_state(self.config.account_address),
            )
            account_value, withdrawable = _unified_usdc_values(spot_state)

        result = {
            "accountValue": account_value,
            "totalMarginUsed": _finite_number(
                summary.get("totalMarginUsed"), "marginSummary.totalMarginUsed"
            ),
            "totalNotionalPosition": _finite_number(
                summary.get("totalNtlPos"), "marginSummary.totalNtlPos"
            ),
            "withdrawable": withdrawable,
            "positions": positions,
            "accountMode": account_mode,
        }
        return self._cache_set("account_state", result)

    def fresh_account_state(self) -> dict[str, Any]:
        return self.account_state(fresh=True)

    def open_orders(self, coin: str | None = None, fresh: bool = False) -> list[dict[str, Any]]:
        selected = self._coin(coin) if coin else None
        output = None if fresh else self._cache_get("open_orders", 1.25)
        if output is None:
            rows = self._read(
                "frontend_open_orders",
                lambda: self.info.frontend_open_orders(self.config.account_address),
            )
            output = []
            for row in rows:
                output.append(
                    {
                        "coin": row.get("coin"),
                        "oid": _integer(row.get("oid"), "openOrder.oid"),
                        "cloid": row.get("cloid"),
                        "side": row.get("side"),
                        "price": _finite_number(row.get("limitPx"), "openOrder.limitPx"),
                        "size": _finite_number(row.get("sz"), "openOrder.sz"),
                        "originalSize": _finite_number(
                            row.get("origSz") if row.get("origSz") is not None and row.get("origSz") != "" else row.get("sz"),
                            "openOrder.origSz",
                        ),
                        "reduceOnly": bool(row.get("reduceOnly")),
                        "tif": row.get("tif"),
                        "orderType": row.get("orderType"),
                        "isTrigger": bool(row.get("isTrigger")),
                        "triggerPrice": _finite_number(row.get("triggerPx"), "openOrder.triggerPx"),
                        "timestamp": _integer(row.get("timestamp"), "openOrder.timestamp"),
                    }
                )
            self._cache_set("open_orders", output)
        return [dict(row) for row in output if selected is None or row.get("coin") == selected]

    def fresh_open_orders(self, coin: str | None = None) -> list[dict[str, Any]]:
        return self.open_orders(coin, fresh=True)

    def mids(self) -> dict[str, float]:
        cached = self._cache_get("mids", 1.0)
        if cached is not None:
            return cached
        rows = self._read("all_mids", self.info.all_mids)
        result = {
            coin: _finite_number(rows[coin], f"mids.{coin}")
            for coin in SUPPORTED_COINS
            if coin in rows
        }
        invalid = [coin for coin, price in result.items() if price <= 0]
        if invalid:
            raise GatewayError(f"Invalid non-positive mids: {invalid}")
        return self._cache_set("mids", result)

    def candles(self, coin: str, interval: str, limit: int = 1000) -> list[dict[str, Any]]:
        coin = self._coin(coin)
        if interval not in INTERVAL_MS:
            raise GatewayError(f"Unsupported interval: {interval}")
        limit = max(50, min(int(limit), 1500))
        end_ms = int(time.time() * 1000)
        start_ms = end_ms - INTERVAL_MS[interval] * (limit + 5)
        rows = self._read(
            "candles_snapshot",
            lambda: self.info.candles_snapshot(coin, interval, start_ms, end_ms),
        )[-limit:]
        return [
            {
                "time": _integer(row.get("t"), "candle.t") // 1000,
                "openTime": _integer(row.get("t"), "candle.t"),
                "closeTime": _integer(row.get("T"), "candle.T"),
                "open": _finite_number(row.get("o"), "candle.o"),
                "high": _finite_number(row.get("h"), "candle.h"),
                "low": _finite_number(row.get("l"), "candle.l"),
                "close": _finite_number(row.get("c"), "candle.c"),
                "volume": _finite_number(row.get("v"), "candle.v"),
            }
            for row in rows
        ]

    def fills_since(self, start_ms: int) -> list[dict[str, Any]]:
        rows = self._read(
            "user_fills_by_time",
            lambda: self.info.user_fills_by_time(
                self.config.account_address,
                int(start_ms),
                aggregate_by_time=False,
            ),
        )
        return [
            {
                "coin": row.get("coin"),
                "oid": _integer(row.get("oid"), "fill.oid"),
                "cloid": row.get("cloid"),
                "price": _finite_number(row.get("px"), "fill.px"),
                "size": _finite_number(row.get("sz"), "fill.sz"),
                "side": row.get("side"),
                "direction": row.get("dir"),
                "closedPnl": _finite_number(row.get("closedPnl"), "fill.closedPnl"),
                "fee": _finite_number(row.get("fee"), "fill.fee"),
                "time": _integer(row.get("time"), "fill.time"),
                "hash": row.get("hash"),
            }
            for row in rows
        ]

    def order_status(self, oid: int) -> dict[str, Any] | None:
        raw = self._read(
            f"order_status:{oid}",
            lambda: self.info.query_order_by_oid(self.config.account_address, int(oid)),
        )
        if not isinstance(raw, dict) or raw.get("status") != "order":
            return None
        wrapper = raw.get("order") or {}
        order = wrapper.get("order") if isinstance(wrapper, dict) else {}
        if not isinstance(order, dict):
            order = {}
        return {
            "oid": _integer(order.get("oid") or oid, "orderStatus.oid"),
            "cloid": order.get("cloid"),
            "coin": order.get("coin"),
            "side": order.get("side"),
            "price": _finite_number(order.get("limitPx"), "orderStatus.limitPx"),
            "size": _finite_number(
                order.get("sz") if order.get("sz") is not None and order.get("sz") != "" else order.get("origSz"),
                "orderStatus.sz",
            ),
            "reduceOnly": bool(order.get("reduceOnly")),
            "triggerPrice": _finite_number(order.get("triggerPx"), "orderStatus.triggerPx"),
            "orderType": order.get("orderType"),
            "timestamp": _integer(order.get("timestamp"), "orderStatus.timestamp"),
            "status": wrapper.get("status") if isinstance(wrapper, dict) else None,
        }

    def preview_ladder(self, coin: str, galka_price: float, total_notional: float) -> list[LadderLevel]:
        return build_ladder(galka_price, total_notional, self.sz_decimals(coin))

    @staticmethod
    def _response(response: Any, expected_type: str | None = None) -> dict[str, Any]:
        if not isinstance(response, dict) or response.get("status") != "ok":
            raise GatewayError(f"Hyperliquid rejected request: {response}")
        payload = response.get("response")
        if not isinstance(payload, dict):
            raise GatewayError(f"Unexpected Hyperliquid response: {response}")
        if expected_type and payload.get("type") != expected_type:
            raise GatewayError(
                f"Unexpected Hyperliquid response type: {payload.get('type')!r}, expected {expected_type!r}"
            )
        return payload

    def set_leverage(self, coin: str) -> dict[str, Any]:
        self._require_live_write("set leverage")
        coin = self._coin(coin)
        leverage = min(self.config.leverage, self.max_leverage(coin))
        with self._io_lock:
            response = self.exchange.update_leverage(leverage, coin, self.config.isolated is False)
        self._invalidate("account_state")
        self._response(response, "default")
        self._invalidate("account_state")
        return response

    def place_entry_with_target(
        self,
        coin: str,
        level: LadderLevel,
        galka_price: float,
        entry_cloid: str | None = None,
        target_cloid: str | None = None,
    ) -> EntryWithTarget:
        self._require_live_write("place entry and target")
        coin = self._coin(coin)
        target = round_perp_price(galka_price, self.sz_decimals(coin))
        requests = [
            {
                "coin": coin,
                "is_buy": True,
                "sz": level.size,
                "limit_px": level.price,
                "order_type": {"limit": {"tif": "Alo"}},
                "reduce_only": False,
                "cloid": self._cloid(entry_cloid),
            },
            {
                "coin": coin,
                "is_buy": False,
                "sz": level.size,
                "limit_px": target,
                "order_type": {
                    "trigger": {"isMarket": False, "triggerPx": target, "tpsl": "tp"}
                },
                "reduce_only": True,
                "cloid": self._cloid(target_cloid),
            },
        ]
        with self._io_lock:
            response = self.exchange.bulk_orders(requests, grouping="normalTpsl")
        self._invalidate("open_orders", "account_state")
        orders = self._parse_order_response(
            response,
            [level, None],
            [entry_cloid, target_cloid],
        )
        if len(orders) != 2:
            raise GatewayError("Hyperliquid did not return both entry and target orders")
        self._invalidate("open_orders", "account_state")
        return EntryWithTarget(
            entry=PlacedOrder(
                oid=orders[0].oid,
                status=orders[0].status,
                level=level.index,
                price=level.price,
                size=level.size,
                cloid=entry_cloid,
            ),
            target=PlacedOrder(
                oid=orders[1].oid,
                status=orders[1].status,
                level=level.index,
                price=target,
                size=level.size,
                cloid=target_cloid,
            ),
        )

    def place_or_replace_target(
        self,
        coin: str,
        quantity: float,
        galka_price: float,
        existing_oid: int | None = None,
        cloid: str | None = None,
    ) -> PlacedOrder:
        self._require_live_write("place or replace target")
        coin = self._coin(coin)
        quantity = round_size_down(abs(quantity), self.sz_decimals(coin))
        if quantity <= 0:
            raise GatewayError("Target quantity rounded to zero")
        target = round_perp_price(galka_price, self.sz_decimals(coin))
        order_type = {"limit": {"tif": "Gtc"}}
        with self._io_lock:
            if existing_oid:
                response = self.exchange.modify_order(
                    existing_oid,
                    coin,
                    False,
                    quantity,
                    target,
                    order_type,
                    reduce_only=True,
                    cloid=self._cloid(cloid),
                )
            else:
                response = self.exchange.order(
                    coin,
                    False,
                    quantity,
                    target,
                    order_type,
                    reduce_only=True,
                    cloid=self._cloid(cloid),
                )
        self._invalidate("open_orders", "account_state")
        rows = self._parse_order_response(response, [None], [cloid])
        if len(rows) != 1:
            raise GatewayError("Unexpected target-order response")
        order = rows[0]
        if order.status in _PENDING_ORDER_STATUSES:
            raise GatewayError(f"Fallback target was not accepted as an active order: {order.status}")
        if order.status == "resting" and order.oid <= 0:
            raise GatewayError("Fallback target is resting without a valid oid")
        self._invalidate("open_orders", "account_state")
        return order

    def cancel_oids(self, coin: str, oids: list[int]) -> dict[str, Any]:
        self._require_live_write("cancel orders")
        coin = self._coin(coin)
        unique = sorted({int(oid) for oid in oids if int(oid) > 0})
        if not unique:
            return {"status": "ok", "response": {"type": "cancel", "data": {"statuses": []}}}
        with self._io_lock:
            response = self.exchange.bulk_cancel([{"coin": coin, "oid": oid} for oid in unique])
        self._invalidate("open_orders", "account_state")
        payload = self._response(response, "cancel")
        statuses = payload.get("data", {}).get("statuses", [])
        if not isinstance(statuses, list) or len(statuses) != len(unique):
            raise GatewayError(f"Incomplete cancel response: {response}")
        errors: list[str] = []
        for oid, status in zip(unique, statuses):
            if status == "success":
                continue
            if isinstance(status, dict) and "error" in status:
                errors.append(f"oid {oid}: {status['error']}")
            else:
                errors.append(f"oid {oid}: unexpected status {status!r}")
        if errors:
            raise GatewayError("; ".join(errors))
        self._invalidate("open_orders", "account_state")
        return response

    def emergency_market_close(self, coin: str, cloid: str | None = None) -> PlacedOrder:
        self._require_live_write("emergency market close")
        coin = self._coin(coin)
        with self._io_lock:
            response = self.exchange.market_close(
                coin,
                slippage=0.02,
                cloid=self._cloid(cloid),
            )
        self._invalidate("open_orders", "account_state")
        if response is None:
            raise GatewayError(f"Hyperliquid found no {coin} position to close")
        rows = self._parse_order_response(response, [None], [cloid])
        if len(rows) != 1:
            raise GatewayError("Unexpected emergency-close response")
        order = rows[0]
        if order.status != "filled":
            raise GatewayError(f"Emergency close was not filled immediately: {order.status}")
        self._invalidate("open_orders", "account_state")
        return order

    def _parse_order_response(
        self,
        response: dict[str, Any],
        levels: list[LadderLevel | None] | None,
        cloids: list[str | None] | None = None,
    ) -> list[PlacedOrder]:
        payload = self._response(response, "order")
        statuses = payload.get("data", {}).get("statuses", [])
        if not isinstance(statuses, list):
            raise GatewayError(f"Unexpected Hyperliquid response: {response}")
        output: list[PlacedOrder] = []
        errors: list[str] = []
        for index, status in enumerate(statuses):
            level = levels[index] if levels and index < len(levels) else None
            cloid = cloids[index] if cloids and index < len(cloids) else None
            if isinstance(status, str):
                if status in _PENDING_ORDER_STATUSES:
                    output.append(
                        PlacedOrder(
                            oid=0,
                            status=status,
                            level=level.index if level else None,
                            price=level.price if level else None,
                            size=level.size if level else None,
                            cloid=cloid,
                        )
                    )
                else:
                    errors.append(f"Unknown order status: {status}")
                continue
            if not isinstance(status, dict):
                errors.append(f"Unexpected order status: {status}")
                continue
            if "error" in status:
                errors.append(str(status["error"]))
                continue
            if "resting" in status:
                oid = int(status["resting"]["oid"])
                state = "resting"
            elif "filled" in status:
                oid = int(status["filled"].get("oid") or 0)
                state = "filled"
            else:
                errors.append(f"Unknown order status: {status}")
                continue
            output.append(
                PlacedOrder(
                    oid=oid,
                    status=state,
                    level=level.index if level else None,
                    price=level.price if level else None,
                    size=level.size if level else None,
                    cloid=cloid,
                )
            )
        if errors:
            raise GatewayError("; ".join(errors))
        if levels is not None and len(output) != len(levels):
            raise GatewayError("Hyperliquid returned an incomplete order-status list")
        return output

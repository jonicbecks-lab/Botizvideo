from __future__ import annotations

import time
from typing import Any

from .hyperliquid_gateway import (
    GatewayError,
    HyperliquidGateway,
    PlacedOrder,
    _finite_number,
    _integer,
    _parse_user_abstraction,
    _unified_usdc_values,
)
from .live_ladder import round_perp_price, round_size_down


class MemHyperliquidGateway(HyperliquidGateway):
    """Hyperliquid gateway variant that supports every active main-dex perp."""

    def __init__(self, config):
        super().__init__(config)
        self._coin_lookup = {
            str(name).upper(): str(name)
            for name in self._universe
        }

    def _coin(self, value: str) -> str:
        raw = str(value or "").strip()
        if not raw:
            raise GatewayError("Coin is required")
        normalized = raw.upper().replace("/USDT", "").replace("/USD", "")
        normalized = normalized.replace("-USDT", "").replace("-USD", "")
        canonical = self._coin_lookup.get(normalized)
        if canonical is None:
            raise GatewayError(f"Unsupported Hyperliquid perp: {value}")
        meta = self._universe.get(canonical) or {}
        if meta.get("isDelisted"):
            raise GatewayError(f"Hyperliquid perp is delisted: {canonical}")
        return canonical

    def market_catalog(self) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        for name, meta in self._universe.items():
            if meta.get("isDelisted"):
                continue
            output.append(
                {
                    "name": name,
                    "szDecimals": int(meta.get("szDecimals", 0)),
                    "maxLeverage": int(meta.get("maxLeverage", 1)),
                    "onlyIsolated": bool(meta.get("onlyIsolated")),
                    "marginMode": meta.get("marginMode"),
                }
            )
        return sorted(output, key=lambda row: row["name"].upper())

    def account_state(self, fresh: bool = False) -> dict[str, Any]:
        cached = None if fresh else self._cache_get("mem_account_state", 1.0)
        if cached is not None:
            return cached
        state = self._read("user_state", lambda: self.info.user_state(self.config.account_address))
        positions: dict[str, dict[str, Any]] = {}
        for row in state.get("assetPositions", []):
            position = row.get("position", {})
            coin = position.get("coin")
            if not coin:
                continue
            positions[str(coin)] = {
                "coin": str(coin),
                "size": _finite_number(position.get("szi"), f"{coin}.position.szi"),
                "entryPrice": _finite_number(position.get("entryPx"), f"{coin}.position.entryPx"),
                "liquidationPrice": _finite_number(
                    position.get("liquidationPx"), f"{coin}.position.liquidationPx"
                ),
                "marginUsed": _finite_number(position.get("marginUsed"), f"{coin}.position.marginUsed"),
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
        return self._cache_set("mem_account_state", result)

    def fresh_account_state(self) -> dict[str, Any]:
        return self.account_state(fresh=True)

    def mids(self) -> dict[str, float]:
        cached = self._cache_get("mem_mids", 0.75)
        if cached is not None:
            return cached
        rows = self._read("all_mids", self.info.all_mids)
        result: dict[str, float] = {}
        for coin in self._universe:
            if coin not in rows or self._universe[coin].get("isDelisted"):
                continue
            price = _finite_number(rows[coin], f"mids.{coin}")
            if price > 0:
                result[coin] = price
        return self._cache_set("mem_mids", result)

    def set_leverage_value(self, coin: str, leverage: int) -> dict[str, Any]:
        self._require_live_write("set leverage")
        coin = self._coin(coin)
        leverage = int(leverage)
        maximum = self.max_leverage(coin)
        if leverage < 1 or leverage > maximum:
            raise GatewayError(f"{coin} leverage must be between 1x and {maximum}x")
        with self._io_lock:
            response = self.exchange.update_leverage(leverage, coin, False)
        self._invalidate("account_state", "mem_account_state")
        self._response(response, "default")
        return response

    def place_limit_order(
        self,
        *,
        coin: str,
        is_buy: bool,
        size: float,
        price: float,
        reduce_only: bool,
        tif: str,
        cloid: str | None,
    ) -> PlacedOrder:
        self._require_live_write("place limit order")
        coin = self._coin(coin)
        size = round_size_down(abs(float(size)), self.sz_decimals(coin))
        price = round_perp_price(float(price), self.sz_decimals(coin))
        if tif not in {"Alo", "Gtc", "Ioc"}:
            raise GatewayError(f"Unsupported TIF: {tif}")
        with self._io_lock:
            response = self.exchange.order(
                coin,
                bool(is_buy),
                size,
                price,
                {"limit": {"tif": tif}},
                reduce_only=bool(reduce_only),
                cloid=self._cloid(cloid),
            )
        self._invalidate("open_orders", "account_state", "mem_account_state")
        rows = self._parse_order_response(response, [None], [cloid])
        if len(rows) != 1:
            raise GatewayError("Unexpected limit-order response")
        order = rows[0]
        return PlacedOrder(
            oid=order.oid,
            status=order.status,
            price=price,
            size=size,
            cloid=cloid,
        )

    def fresh_open_orders(self, coin: str | None = None) -> list[dict[str, Any]]:
        self._invalidate("open_orders")
        return super().open_orders(coin, fresh=True)

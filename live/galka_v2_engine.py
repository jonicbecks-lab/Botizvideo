from __future__ import annotations

import math
import time
from copy import deepcopy
from typing import Any

from .engine import ACTIVE_STATUSES, RECOVERY_STATUS, LiveEngineError, new_cloid, now_iso, now_ms
from .galka_v2_strategy import (
    UPPER_NET_ROE_TARGET,
    V2_LEVERAGE,
    V2_MARGIN_USD,
    V2_TOTAL_NOTIONAL,
    V2Level,
    build_v2_plan,
    upper_take_profit_price,
)
from .hyperliquid_compat import CompatibleGalkaLiveEngine, CompatibleHyperliquidGateway
from .hyperliquid_gateway import GatewayError, PlacedOrder
from .live_ladder import round_perp_price

STRATEGY_VERSION = "galka-v2-fibo-100"


class GalkaV2Gateway(CompatibleHyperliquidGateway):
    """Hyperliquid gateway additions used only by GALKA V2."""

    def place_entries_batch(
        self,
        coin: str,
        levels: list[V2Level],
        entry_cloids: list[str],
    ) -> list[PlacedOrder]:
        self._require_live_write("place GALKA V2 entries")
        normalized = self._coin(coin)
        if len(levels) != len(entry_cloids):
            raise GatewayError("GALKA V2 entry cloid/level count mismatch")
        requests: list[dict[str, Any]] = []
        ladder_levels = []
        for level, cloid in zip(levels, entry_cloids):
            requests.append(
                {
                    "coin": normalized,
                    "is_buy": True,
                    "sz": level.size,
                    "limit_px": level.price,
                    "order_type": {"limit": {"tif": "Alo"}},
                    "reduce_only": False,
                    "cloid": self._cloid(cloid),
                }
            )
            ladder_levels.append(level.to_ladder_level())

        def submit() -> list[PlacedOrder]:
            with self._io_lock:
                response = self.exchange.bulk_orders(requests, grouping="na")
            self._invalidate("open_orders", "account_state")
            rows = self._parse_order_response(response, ladder_levels, entry_cloids)
            if len(rows) != len(levels):
                raise GatewayError(f"Incomplete GALKA V2 batch response: {len(rows)}/{len(levels)}")
            output: list[PlacedOrder] = []
            for level, cloid, row in zip(levels, entry_cloids, rows):
                output.append(
                    PlacedOrder(
                        oid=row.oid,
                        status=row.status,
                        level=level.index,
                        price=level.price,
                        size=level.size,
                        cloid=cloid,
                    )
                )
            return output

        return self._timed("v2_batch_place_9_entries", submit)


class GalkaV2Engine(CompatibleGalkaLiveEngine):
    """Two-zone GALKA V2: Fibonacci upper block + deeper lower GALKA block."""

    @staticmethod
    def _is_v2(campaign: dict[str, Any] | None) -> bool:
        return bool(campaign and campaign.get("strategyVersion") == STRATEGY_VERSION)

    def _require_v2_config(self) -> None:
        if int(self.config.leverage) != V2_LEVERAGE:
            raise LiveEngineError(
                f"GALKA V2 requires {V2_LEVERAGE}x isolated leverage; current config is {self.config.leverage}x"
            )

    @staticmethod
    def _right_high(research_setup: dict[str, Any] | None) -> float:
        setup = research_setup if isinstance(research_setup, dict) else {}
        try:
            high = float(setup.get("rightBoundaryPrice") or 0)
        except (TypeError, ValueError) as exc:
            raise LiveEngineError("GALKA V2 needs the right leg marked on the chart") from exc
        if not math.isfinite(high) or high <= 0:
            raise LiveEngineError("GALKA V2 needs the right leg marked on the chart")
        return high

    def preview_v2(
        self,
        coin: str,
        galka_price: float,
        research_setup: dict[str, Any] | None,
    ) -> dict[str, Any]:
        coin = self._coin(coin)
        self._require_v2_config()
        galka = float(galka_price)
        if not math.isfinite(galka) or galka <= 0:
            raise LiveEngineError("Цена GALKA должна быть конечным числом больше нуля")
        setup = research_setup if isinstance(research_setup, dict) else {}
        right_high = self._right_high(setup)
        anchor_price = float(setup.get("anchorPrice") or galka)
        if abs(anchor_price - galka) > max(1e-8, galka * 1e-5):
            raise LiveEngineError("Якорь структуры должен находиться на уровне GALKA")
        if right_high <= galka:
            raise LiveEngineError("Правая нога GALKA должна расти вверх от уровня GALKA")

        plan = build_v2_plan(
            galka,
            right_high,
            self.gateway.sz_decimals(coin),
            total_notional=V2_TOTAL_NOTIONAL,
            entry_fee_rate=self.config.maker_fee_rate,
            exit_fee_rate=self.config.maker_fee_rate,
        )
        mid = float(self.gateway.mids().get(coin) or 0)
        if mid <= 0:
            raise LiveEngineError(f"Нет текущей цены {coin}")
        highest_entry = max(level.price for level in plan.levels)
        if mid <= highest_entry:
            raise LiveEngineError(
                f"Цена {coin} уже прошла верхний Fibonacci-вход. Перестрой GALKA по свежей правой ноге."
            )
        account = self.gateway.fresh_account_state()
        actual_notional = sum(level.notional for level in plan.levels)
        return {
            "strategyVersion": STRATEGY_VERSION,
            "coin": coin,
            "galkaPrice": galka,
            "rightHigh": right_high,
            "currentPrice": mid,
            "levels": [level.to_dict() for level in plan.levels],
            "requestedNotional": V2_TOTAL_NOTIONAL,
            "actualNotional": actual_notional,
            "requiredMargin": actual_notional / V2_LEVERAGE,
            "marginCap": V2_MARGIN_USD,
            "leverage": V2_LEVERAGE,
            "isolated": True,
            "upperShare": plan.upper_share,
            "lowerShare": plan.lower_share,
            "weightedAverage": plan.weighted_average,
            "fullFillNetAtGalka": plan.full_fill_net_at_galka,
            "upperNetRoeTarget": UPPER_NET_ROE_TARGET,
            "makerFeeRate": self.config.maker_fee_rate,
            "takerFeeRate": self.config.taker_fee_rate,
            "accountValue": account["accountValue"],
            "withdrawable": account["withdrawable"],
            "liveEnabled": self.config.live_enabled,
        }

    def create_campaign_v2(
        self,
        coin: str,
        galka_price: float,
        confirmation: str,
        research_setup: dict[str, Any] | None,
    ) -> dict[str, Any]:
        normalized = self._coin(coin)
        self.gateway.begin_trace("create_campaign_v2")
        success = False
        try:
            result = self._create_campaign_v2_fast(
                normalized, float(galka_price), confirmation, research_setup
            )
            success = True
            return result
        finally:
            self._record_latency(
                "выставление GALKA V2", normalized, self.gateway.finish_trace(), success
            )

    def create_campaign(self, coin: str, galka_price: float, confirmation: str) -> dict[str, Any]:
        raise LiveEngineError("GALKA V2 создаётся только из трёхточечной разметки на графике")

    def _create_campaign_v2_fast(
        self,
        coin: str,
        galka_price: float,
        confirmation: str,
        research_setup: dict[str, Any] | None,
    ) -> dict[str, Any]:
        self._require_live_writes()
        self._require_v2_config()
        if confirmation != "PLACE_REAL_ORDERS":
            raise LiveEngineError("Не подтверждена отправка реальных ордеров")

        with self.action_lock:
            with self.lock:
                system = self.state.get("system", {})
                if system.get("safeMode"):
                    raise LiveEngineError(
                        "SAFE MODE: " + (system.get("safeModeReason") or "требуется сверка")
                    )
                if self.monitor_thread.ident is not None and not self.monitor_thread.is_alive():
                    self._set_safe_mode_locked("Фоновый LIVE-монитор остановлен")
                    self._save_locked()
                    raise LiveEngineError("SAFE MODE: фоновый LIVE-монитор остановлен")
                if self._active_campaign_locked(coin):
                    raise LiveEngineError(f"Уже активна GALKA {coin}")

            preview = self.preview_v2(coin, galka_price, research_setup)
            account = self.gateway.fresh_account_state()
            all_orders = self.gateway.fresh_open_orders()
            selected_position = self._position_size(account, coin)
            selected_orders = [row for row in all_orders if row.get("coin") == coin]
            if abs(selected_position) > self._size_tolerance(coin):
                raise LiveEngineError(
                    f"На {coin} уже есть реальная позиция {selected_position:g}. Новая GALKA не создана."
                )
            if selected_orders:
                raise LiveEngineError(
                    f"На {coin} уже есть {len(selected_orders)} открытых ордеров. Сначала выполни сверку."
                )

            with self.lock:
                reserved_margin = sum(
                    float(active.get("actualNotional") or active.get("requestedNotional") or 0)
                    / max(1, int(active.get("leverage") or V2_LEVERAGE))
                    for active in self._active_campaigns_locked()
                )
            allowed_margin = max(0.0, account["accountValue"] * self.config.max_margin_fraction)
            aggregate_margin = reserved_margin + preview["requiredMargin"]
            if aggregate_margin > allowed_margin:
                raise LiveEngineError(
                    f"Общий риск-лимит маржи: зарезервировано ${reserved_margin:.2f}, "
                    f"GALKA V2 нужно ${preview['requiredMargin']:.2f}, "
                    f"разрешено ${allowed_margin:.2f}."
                )

            self.gateway.set_leverage(coin)
            levels = [V2Level(**row) for row in preview["levels"]]
            campaign_id = f"HLV2-{coin}-{now_ms()}-{new_cloid()[-6:]}"
            campaign = self._new_v2_campaign(
                campaign_id, coin, galka_price, preview, levels, research_setup or {}
            )
            with self.lock:
                if self._active_campaign_locked(coin):
                    raise LiveEngineError(f"Другая LIVE-кампания {coin} успела стать активной")
                self.state.setdefault("campaigns", {})[coin] = campaign
                self._save_locked()

            try:
                placed = self.gateway.place_entries_batch(
                    coin,
                    levels,
                    [str(row["entryCloid"]) for row in campaign["levels"]],
                )
                with self.lock:
                    for level_state, order in zip(campaign["levels"], placed):
                        level_state["oid"] = order.oid or None
                        level_state["status"] = order.status
                        if order.oid:
                            campaign["entryOidMap"][str(order.oid)] = int(level_state["index"])
                    campaign["updatedAt"] = now_iso()
                    self._save_locked()

                open_orders = self.gateway.fresh_open_orders(coin)
                with self.lock:
                    self._register_delayed_orders(campaign, open_orders)
                entry_open = [
                    row for row in open_orders if self._entry_owner(campaign, row) is not None
                ]
                account = self.gateway.fresh_account_state()
                actual = self._position_size(account, coin)
                if len(entry_open) < len(levels) and actual <= self._size_tolerance(coin):
                    raise LiveEngineError(
                        f"Биржа подтвердила только {len(entry_open)} из {len(levels)} V2-входов"
                    )

                with self.lock:
                    campaign["status"] = "open" if actual > self._size_tolerance(coin) else "waiting"
                    campaign["actualPositionSize"] = actual
                    campaign["updatedAt"] = now_iso()
                    self._event_locked(
                        "live",
                        f"{coin}: GALKA V2 создана; 9 входов, маржа до ${V2_MARGIN_USD:.0f}",
                        campaignId=campaign_id,
                        upperShare=preview["upperShare"],
                        lowerShare=preview["lowerShare"],
                    )
                    self._save_locked()

                # Close the protection gap immediately if any entry filled during placement.
                if actual > self._size_tolerance(coin):
                    self._sync_campaign(campaign)
                return deepcopy(campaign)
            except Exception as exc:
                self._creation_failure(campaign, exc)
                raise LiveEngineError(
                    f"GALKA V2 создана не полностью и переведена в recovery: {exc}"
                ) from exc

    def _new_v2_campaign(
        self,
        campaign_id: str,
        coin: str,
        galka_price: float,
        preview: dict[str, Any],
        levels: list[V2Level],
        research_setup: dict[str, Any],
    ) -> dict[str, Any]:
        created_ms = now_ms()
        rows = []
        entry_cloid_map: dict[str, int] = {}
        for level in levels:
            cloid = new_cloid()
            entry_cloid_map[cloid] = level.index
            rows.append(
                {
                    **level.to_dict(),
                    "oid": None,
                    "tpOid": None,
                    "entryCloid": cloid,
                    "targetCloid": None,
                    "status": "new",
                    "filledSize": 0.0,
                    "averageFillPrice": 0.0,
                }
            )
        return {
            "id": campaign_id,
            "coin": coin,
            "status": "placing",
            "strategyVersion": STRATEGY_VERSION,
            "galkaPrice": float(galka_price),
            "rightHigh": float(preview["rightHigh"]),
            "researchSetup": deepcopy(research_setup),
            "createdAt": now_iso(),
            "createdMs": created_ms,
            "updatedAt": now_iso(),
            "leverage": V2_LEVERAGE,
            "isolated": True,
            "requestedNotional": V2_TOTAL_NOTIONAL,
            "actualNotional": preview["actualNotional"],
            "requiredMargin": preview["requiredMargin"],
            "upperShare": preview["upperShare"],
            "lowerShare": preview["lowerShare"],
            "fullFillNetAtGalka": preview["fullFillNetAtGalka"],
            "levels": rows,
            "entryOidMap": {},
            "targetOidMap": {},
            "entryCloidMap": entry_cloid_map,
            "targetCloidMap": {},
            "fallbackTargetOid": None,
            "fallbackTargetCloid": None,
            "managedNetSize": 0.0,
            "actualPositionSize": 0.0,
            "hadPosition": False,
            "cycleDeepest": 0,
            "l1Cycles": 0,
            "l1RealizedPnl": 0.0,
            "cycleClosedPnl": 0.0,
            "cycleFees": 0.0,
            "seenFills": [],
            "unknownSeenFills": [],
            "fillCursorMs": max(0, created_ms - 60_000),
            "abortAfterClose": False,
            "autoRearmBlocked": True,
            "recoveryReason": None,
            "recoveryZeroConfirmations": 0,
            "lastError": None,
            "v2UpperDone": False,
            "v2LowerActivated": False,
            "v2TargetMode": None,
            "v2TargetPrice": None,
            "v2EntryFees": 0.0,
            "v2ExitFees": 0.0,
            "v2UpperRealizedPnl": 0.0,
        }

    def _apply_new_fills(self, campaign: dict[str, Any], fills: list[dict[str, Any]]) -> None:
        if not self._is_v2(campaign):
            return super()._apply_new_fills(campaign, fills)
        before = set(campaign.get("seenFills", []))
        fresh = [fill for fill in fills if self._fill_key(fill) not in before]
        super()._apply_new_fills(campaign, fills)
        for fill in fresh:
            kind = fill.get("_ownerKind")
            fee = float(fill.get("fee") or 0)
            if kind == "entry" and fill.get("side") == "B":
                campaign["v2EntryFees"] = float(campaign.get("v2EntryFees") or 0) + fee
            elif kind == "target" and fill.get("side") == "A":
                campaign["v2ExitFees"] = float(campaign.get("v2ExitFees") or 0) + fee

    def _v2_lower_filled(self, campaign: dict[str, Any]) -> bool:
        return any(
            level.get("zone") == "lower" and float(level.get("filledSize") or 0) > 0
            for level in campaign.get("levels", [])
        )

    def _cancel_v2_upper_entries(self, campaign: dict[str, Any], open_orders: list[dict[str, Any]]) -> None:
        upper_indexes = {
            int(level["index"])
            for level in campaign.get("levels", [])
            if level.get("zone") == "upper"
        }
        selected = [
            row for row in open_orders
            if self._entry_owner(campaign, row) in upper_indexes
        ]
        self._cancel_specific_and_verify(campaign["coin"], [row["oid"] for row in selected])

    def _v2_upper_fill_totals(self, campaign: dict[str, Any]) -> tuple[float, float]:
        qty = 0.0
        notional = 0.0
        for level in campaign.get("levels", []):
            if level.get("zone") != "upper":
                continue
            filled = float(level.get("filledSize") or 0)
            avg = float(level.get("averageFillPrice") or 0)
            qty += filled
            notional += filled * avg
        return qty, notional

    def _ensure_target_coverage(
        self,
        campaign: dict[str, Any],
        open_orders: list[dict[str, Any]],
        actual_size: float,
    ) -> None:
        if not self._is_v2(campaign):
            return super()._ensure_target_coverage(campaign, open_orders, actual_size)
        self._ensure_v2_target(campaign, open_orders, actual_size)

    def _ensure_v2_target(
        self,
        campaign: dict[str, Any],
        open_orders: list[dict[str, Any]],
        actual_size: float,
    ) -> None:
        coin = campaign["coin"]
        tolerance = self._size_tolerance(coin)
        if actual_size <= tolerance:
            return

        lower_activated = bool(campaign.get("v2LowerActivated")) or self._v2_lower_filled(campaign)
        if lower_activated and not campaign.get("v2LowerActivated"):
            self._cancel_v2_upper_entries(campaign, open_orders)
            open_orders = self.gateway.fresh_open_orders(coin)
            with self.lock:
                campaign["v2LowerActivated"] = True
                campaign["v2UpperDone"] = True
                self._event_locked(
                    "live",
                    f"{coin}: нижняя зона активирована; верхние входы сняты, выход переключён на GALKA",
                    campaignId=campaign["id"],
                )
                self._save_locked()

        if lower_activated:
            target_price = round_perp_price(
                float(campaign["galkaPrice"]), self.gateway.sz_decimals(coin)
            )
            target_mode = "galka"
        else:
            qty, entry_notional = self._v2_upper_fill_totals(campaign)
            if qty <= tolerance or entry_notional <= 0:
                return
            target_price = round_perp_price(
                upper_take_profit_price(
                    quantity=qty,
                    entry_notional=entry_notional,
                    entry_fees_paid=float(campaign.get("v2EntryFees") or 0),
                    leverage=V2_LEVERAGE,
                    net_roe_target=UPPER_NET_ROE_TARGET,
                    exit_fee_rate=self.config.maker_fee_rate,
                ),
                self.gateway.sz_decimals(coin),
            )
            target_mode = "upper_1pct_net"

        fallback_oid = int(campaign.get("fallbackTargetOid") or 0)
        fallback_open = next(
            (
                row for row in open_orders
                if int(row.get("oid") or 0) == fallback_oid
                or self._target_owner(campaign, row) == 0
            ),
            None,
        )
        existing_oid = int(fallback_open.get("oid") or 0) if fallback_open else None
        existing_size = float(fallback_open.get("size") or 0) if fallback_open else 0.0
        existing_price = float(fallback_open.get("price") or 0) if fallback_open else 0.0
        price_tolerance = max(1e-9, target_price * 1e-7)
        if (
            fallback_open
            and abs(existing_size - actual_size) <= tolerance
            and abs(existing_price - target_price) <= price_tolerance
            and campaign.get("v2TargetMode") == target_mode
        ):
            with self.lock:
                campaign["status"] = "closing"
            return

        cloid = campaign.get("fallbackTargetCloid") or new_cloid()
        placed = self.gateway.place_or_replace_target(
            coin,
            actual_size,
            target_price,
            existing_oid,
            cloid,
        )
        with self.lock:
            campaign["v2TargetMode"] = target_mode
            campaign["v2TargetPrice"] = target_price
            if placed.status == "filled":
                campaign["fallbackTargetOid"] = None
                campaign["fallbackTargetCloid"] = None
            else:
                campaign["fallbackTargetOid"] = placed.oid
                campaign["fallbackTargetCloid"] = cloid
                campaign.setdefault("targetOidMap", {})[str(placed.oid)] = 0
                campaign.setdefault("targetCloidMap", {})[cloid] = 0
            campaign["status"] = "closing"
            campaign["updatedAt"] = now_iso()
            self._event_locked(
                "live",
                f"{coin}: V2 target {target_mode} → {target_price:g}",
                campaignId=campaign["id"],
                size=actual_size,
                price=target_price,
            )
            self._save_locked()

    def _finish_cycle(self, campaign: dict[str, Any]) -> None:
        if not self._is_v2(campaign):
            return super()._finish_cycle(campaign)
        coin = campaign["coin"]
        lower_activated = bool(campaign.get("v2LowerActivated")) or self._v2_lower_filled(campaign)
        cycle_net = float(campaign.get("cycleClosedPnl") or 0) - float(campaign.get("cycleFees") or 0)

        if not lower_activated:
            open_orders = self.gateway.fresh_open_orders(coin)
            self._cancel_v2_upper_entries(campaign, open_orders)
            with self.lock:
                campaign["v2UpperDone"] = True
                campaign["v2UpperRealizedPnl"] = float(campaign.get("v2UpperRealizedPnl") or 0) + cycle_net
                campaign["cycleClosedPnl"] = 0.0
                campaign["cycleFees"] = 0.0
                campaign["cycleDeepest"] = 0
                campaign["managedNetSize"] = 0.0
                campaign["actualPositionSize"] = 0.0
                campaign["hadPosition"] = False
                campaign["fallbackTargetOid"] = None
                campaign["fallbackTargetCloid"] = None
                campaign["v2TargetMode"] = None
                campaign["v2TargetPrice"] = None
                campaign["status"] = "waiting"
                campaign["updatedAt"] = now_iso()
                self._event_locked(
                    "live",
                    f"{coin}: верхний блок закрыт по +1% net; нижняя GALKA остаётся ждать",
                    campaignId=campaign["id"],
                    pnl=cycle_net,
                )
                self._save_locked()
            return

        self._cancel_owned_orders(campaign)
        ok, actual, remaining = self._confirm_flat_and_clean(campaign, reads=3)
        if not ok:
            self._enter_recovery(
                campaign,
                "GALKA V2: финальная очистка после выхода на GALKA не подтверждена",
                actual,
                remaining,
            )
            return
        total_net = float(campaign.get("v2UpperRealizedPnl") or 0) + cycle_net
        with self.lock:
            campaign["status"] = "completed"
            campaign["completedAt"] = now_iso()
            campaign["updatedAt"] = now_iso()
            campaign["hadPosition"] = False
            campaign["managedNetSize"] = 0.0
            campaign["actualPositionSize"] = 0.0
            campaign["finalClosedPnl"] = total_net
            self._event_locked(
                "live",
                f"{coin}: GALKA V2 закрыта на возврате к GALKA",
                campaignId=campaign["id"],
                pnl=total_net,
            )
            self._save_locked()

    def status(self) -> dict[str, Any]:
        data = super().status()
        data["strategyVersion"] = STRATEGY_VERSION
        data["leverage"] = V2_LEVERAGE
        data["totalNotional"] = V2_TOTAL_NOTIONAL
        data["marginCap"] = V2_MARGIN_USD
        data["upperNetRoeTarget"] = UPPER_NET_ROE_TARGET
        return data

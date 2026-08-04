"""Compatibility names kept for existing launch scripts.

This module also carries the optimized LIVE execution path used by the persistent
server. The safety model remains fail-closed: a campaign is persisted before any
exchange write, client order ids are known in advance, and ambiguous exchange
results still enter recovery.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .engine import ACTIVE_STATUSES, GalkaLiveEngine, LiveEngineError, now_iso, now_ms
from .hyperliquid_gateway import (
    EntryWithTarget,
    GatewayError,
    HyperliquidGateway,
    PlacedOrder,
)
from .live_ladder import LadderLevel, round_perp_price


class CompatibleHyperliquidGateway(HyperliquidGateway):
    """Gateway with one-request placement for the complete eight-level ladder."""

    def place_ladder_with_targets(
        self,
        coin: str,
        levels: list[LadderLevel],
        galka_price: float,
        entry_cloids: list[str],
        target_cloids: list[str],
    ) -> list[EntryWithTarget]:
        self._require_live_write("place complete ladder and targets")
        coin = self._coin(coin)
        if not levels or len(levels) != len(entry_cloids) or len(levels) != len(target_cloids):
            raise GatewayError("Incomplete ladder batch parameters")

        target = round_perp_price(galka_price, self.sz_decimals(coin))
        requests: list[dict[str, Any]] = []
        response_levels: list[LadderLevel | None] = []
        response_cloids: list[str | None] = []

        for level, entry_cloid, target_cloid in zip(levels, entry_cloids, target_cloids):
            requests.extend(
                [
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
            )
            response_levels.extend([level, None])
            response_cloids.extend([entry_cloid, target_cloid])

        with self._io_lock:
            response = self.exchange.bulk_orders(requests, grouping="normalTpsl")
        self._invalidate("open_orders", "account_state")
        orders = self._parse_order_response(response, response_levels, response_cloids)
        expected = len(levels) * 2
        if len(orders) != expected:
            raise GatewayError(
                f"Hyperliquid returned {len(orders)} of {expected} ladder orders"
            )

        pairs: list[EntryWithTarget] = []
        for index, level in enumerate(levels):
            entry = orders[index * 2]
            target_order = orders[index * 2 + 1]
            pairs.append(
                EntryWithTarget(
                    entry=PlacedOrder(
                        oid=entry.oid,
                        status=entry.status,
                        level=level.index,
                        price=level.price,
                        size=level.size,
                        cloid=entry_cloids[index],
                    ),
                    target=PlacedOrder(
                        oid=target_order.oid,
                        status=target_order.status,
                        level=level.index,
                        price=target,
                        size=level.size,
                        cloid=target_cloids[index],
                    ),
                )
            )
        self._invalidate("open_orders", "account_state")
        return pairs


class CompatibleGalkaLiveEngine(GalkaLiveEngine):
    """Optimized engine preserving the hardened reconciliation guarantees."""

    def create_campaign(self, coin: str, galka_price: float, confirmation: str) -> dict[str, Any]:
        coin = self._coin(coin)
        self._require_live_writes()
        if confirmation != "PLACE_REAL_ORDERS":
            raise LiveEngineError("Не подтверждена отправка реальных ордеров")

        with self.action_lock:
            with self.lock:
                system = self.state.get("system", {})
                if system.get("safeMode"):
                    raise LiveEngineError(
                        f"SAFE MODE: {system.get('safeModeReason') or 'требуется сверка'}"
                    )
                if self.monitor_thread.ident is not None and not self.monitor_thread.is_alive():
                    self._set_safe_mode_locked("Фоновый LIVE-монитор остановлен")
                    self._save_locked()
                    raise LiveEngineError("SAFE MODE: фоновый LIVE-монитор остановлен")
                if self._active_campaign_locked(coin):
                    raise LiveEngineError(
                        f"Уже активна GALKA {coin}. Для каждой монеты разрешена только одна кампания."
                    )

            preview = self.preview(coin, galka_price)
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
                    / max(1, int(active.get("leverage") or self.config.leverage))
                    for active in self._active_campaigns_locked()
                )
            allowed_margin = max(0.0, account["accountValue"] * self.config.max_margin_fraction)
            aggregate_margin = reserved_margin + preview["requiredMargin"]
            if aggregate_margin > allowed_margin:
                raise LiveEngineError(
                    f"Общий риск-лимит маржи: зарезервировано ${reserved_margin:.2f}, "
                    f"новой GALKA нужно ${preview['requiredMargin']:.2f}, "
                    f"разрешено не более ${allowed_margin:.2f} "
                    f"({self.config.max_margin_fraction:.0%} от капитала ${account['accountValue']:.2f})."
                )

            self.gateway.set_leverage(coin)
            levels = [LadderLevel(**row) for row in preview["levels"]]
            campaign_id = f"HL-{coin}-{now_ms()}"
            campaign = self._new_campaign(campaign_id, coin, galka_price, preview, levels)
            with self.lock:
                if self._active_campaign_locked(coin):
                    raise LiveEngineError(f"Другая LIVE-кампания {coin} успела стать активной")
                self.state.setdefault("campaigns", {})[coin] = campaign
                # Persist all CLOIDs before the single exchange request. If the
                # process stops after acceptance, startup reconciliation can
                # recover every order by ownership rather than guessing.
                self._save_locked()

            try:
                pairs = self.gateway.place_ladder_with_targets(
                    coin,
                    levels,
                    float(galka_price),
                    [str(level["entryCloid"]) for level in campaign["levels"]],
                    [str(level["targetCloid"]) for level in campaign["levels"]],
                )
                with self.lock:
                    for level_state, pair in zip(campaign["levels"], pairs):
                        self._record_pair_locked(campaign, level_state, pair)
                    campaign["updatedAt"] = now_iso()
                    # One durable write replaces eight state writes from the old
                    # sequential placement path.
                    self._save_locked()

                open_orders = self.gateway.fresh_open_orders(coin)
                with self.lock:
                    self._register_delayed_orders(campaign, open_orders)
                entry_open = [
                    row for row in open_orders if self._entry_owner(campaign, row) is not None
                ]
                if len(entry_open) != len(levels):
                    raise LiveEngineError(
                        f"Биржа подтвердила только {len(entry_open)} из {len(levels)} входов"
                    )
                account = self.gateway.fresh_account_state()
                if abs(self._position_size(account, coin)) > self._size_tolerance(coin):
                    raise LiveEngineError("Позиция появилась во время создания; включается recovery")

                with self.lock:
                    campaign["status"] = "waiting"
                    campaign["updatedAt"] = now_iso()
                    self._event_locked(
                        "live",
                        f"{coin}: реальная GALKA {galka_price:g}, пакетно выставлено 8 лимиток с TP",
                        campaignId=campaign_id,
                        actualNotional=preview["actualNotional"],
                    )
                    self._save_locked()
                    return deepcopy(campaign)
            except Exception as exc:
                self._creation_failure(campaign, exc)
                raise LiveEngineError(
                    f"GALKA создана не полностью и переведена в recovery: {exc}"
                ) from exc

    def cancel_waiting_campaign(self, coin: str) -> dict[str, Any]:
        """Fast path for a clean 0/8 campaign; ambiguous cases use the strict parent path."""
        coin = self._coin(coin)
        self._require_live_writes()

        with self.action_lock:
            with self.lock:
                campaign = self._active_campaign_locked(coin)
                if not campaign:
                    raise LiveEngineError(f"Для {coin} нет активной GALKA")
                local_clean = (
                    campaign.get("status") == "waiting"
                    and not campaign.get("hadPosition")
                    and abs(float(campaign.get("managedNetSize") or 0))
                    <= self._size_tolerance(coin)
                    and all(
                        abs(float(level.get("filledSize") or 0)) <= self._size_tolerance(coin)
                        for level in campaign.get("levels", [])
                    )
                )

            if not local_clean:
                # Release the lock path by delegating through the original method
                # outside this action-lock scope is impossible, so reproduce the
                # strict sync gate here before continuing.
                self._sync_campaign(campaign)

            account = self.gateway.fresh_account_state()
            open_orders = self.gateway.fresh_open_orders(coin)
            actual = self._position_size(account, coin)
            if abs(actual) > self._size_tolerance(coin):
                self._enter_recovery(
                    campaign,
                    "Обычная отмена остановлена: биржа уже показывает позицию",
                    actual,
                    open_orders,
                )
                raise LiveEngineError(
                    "Есть реальная позиция. Обычная отмена запрещена; кампания переведена в recovery."
                )

            with self.lock:
                if campaign.get("status") == "recovery":
                    raise LiveEngineError(
                        "Кампания находится в recovery; используй аварийное закрытие или сверку"
                    )
                if campaign.get("status") not in ACTIVE_STATUSES:
                    raise LiveEngineError(
                        f"Кампания уже завершила переход в статус {campaign.get('status')}"
                    )
                campaign["status"] = "canceling"
                campaign["updatedAt"] = now_iso()
                self._save_locked()

            try:
                self._cancel_owned_orders(campaign, open_orders=open_orders)
                # Two independent clean reads preserve confirmation while removing
                # the old four-read delay from the common 0/8 path.
                ok, actual, remaining = self._confirm_flat_and_clean(campaign, reads=2)
                if not ok:
                    self._enter_recovery(
                        campaign,
                        "Отмена не подтверждена фактическим состоянием биржи",
                        actual,
                        remaining,
                    )
                    raise LiveEngineError(
                        "Биржа не подтвердила безопасную отмену; включён recovery"
                    )
            except Exception as exc:
                if campaign.get("status") != "recovery":
                    latest_account = self.gateway.fresh_account_state()
                    latest_orders = self.gateway.fresh_open_orders(coin)
                    self._enter_recovery(
                        campaign,
                        f"Ошибка отмены: {exc}",
                        self._position_size(latest_account, coin),
                        latest_orders,
                    )
                raise LiveEngineError(str(exc)) from exc

            with self.lock:
                campaign["status"] = "canceled"
                campaign["completedAt"] = now_iso()
                campaign["updatedAt"] = now_iso()
                self._event_locked(
                    "live",
                    f"{coin}: GALKA быстро отменена без позиции",
                    campaignId=campaign["id"],
                )
                self._save_locked()
                return deepcopy(campaign)

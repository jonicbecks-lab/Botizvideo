from __future__ import annotations

import json
import math
import os
import threading
import time
import uuid
from copy import deepcopy
from typing import Any

from .config import LiveConfig
from .hyperliquid_gateway import GatewayError, HyperliquidGateway, SUPPORTED_COINS
from .live_ladder import (
    LadderLevel,
    estimated_target_pnl,
    estimated_target_pnl_mixed,
    weighted_average,
)

ACTIVE_STATUSES = {
    "placing",
    "waiting",
    "open",
    "closing",
    "canceling",
    "emergency",
    "recovery",
}
RECOVERY_STATUS = "recovery"


class LiveEngineError(RuntimeError):
    pass


def now_ms() -> int:
    return int(time.time() * 1000)


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def new_cloid() -> str:
    return "0x" + uuid.uuid4().hex


class GalkaLiveEngine:
    """Fail-closed coordinator for manual GALKA campaigns.

    The exchange position is the final source of truth. Local fills are used to
    decide whether an L1 cycle may be automatically rearmed, but any unexplained
    difference between local ownership and the real position moves the campaign
    into recovery and permanently blocks automatic rearm for that cycle.
    """

    def __init__(self, config: LiveConfig, gateway: HyperliquidGateway):
        self.config = config
        self.gateway = gateway
        self.state_path = config.data_dir / "state.json"
        self.lock = threading.RLock()          # in-memory state only
        self.action_lock = threading.Lock()    # serializes all exchange actions
        self.stop_event = threading.Event()
        self.state = self._load_state()
        self.monitor_thread = threading.Thread(
            target=self._monitor_loop,
            name="galka-live-monitor",
            daemon=True,
        )

    def start(self) -> None:
        # Initial reconciliation is synchronous: the browser must never see LIVE
        # as ready before persisted campaigns have been compared with the venue.
        try:
            with self.action_lock:
                self._initial_reconcile()
        except Exception as exc:
            with self.lock:
                self._set_safe_mode_locked(f"Startup reconciliation failed: {exc}")
                self._event_locked("risk", "Стартовая сверка не завершена; включён SAFE MODE")
                self._save_locked()
        self.monitor_thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.monitor_thread.is_alive():
            self.monitor_thread.join(timeout=5)

    @staticmethod
    def _empty_state() -> dict[str, Any]:
        return {
            "version": 3,
            "system": {
                "safeMode": False,
                "safeModeReason": None,
                "stateCorrupt": False,
                "lastReconcileAt": None,
                "lastGlobalCheckAt": None,
                "monitorHeartbeatAt": None,
            },
            "campaigns": {},
            "events": [],
        }

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return self._empty_state()
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("version") not in {1, 2, 3}:
                raise ValueError("unsupported state version")
            data["version"] = 3
            data.setdefault("system", {})
            system = data["system"]
            system.setdefault("safeMode", False)
            system.setdefault("safeModeReason", None)
            system.setdefault("stateCorrupt", False)
            system.setdefault("lastReconcileAt", None)
            system.setdefault("lastGlobalCheckAt", None)
            system.setdefault("monitorHeartbeatAt", None)
            data.setdefault("campaigns", {})
            data.setdefault("events", [])
            for campaign in data["campaigns"].values():
                self._migrate_campaign(campaign)
            return data
        except Exception as exc:
            backup = self.state_path.with_suffix(f".broken-{now_ms()}.json")
            self.state_path.replace(backup)
            state = self._empty_state()
            state["system"].update(
                {
                    "safeMode": True,
                    "safeModeReason": f"Повреждён state-файл: {backup.name}",
                    "stateCorrupt": True,
                }
            )
            state["events"].append(
                {
                    "time": now_iso(),
                    "type": "risk",
                    "message": f"Повреждённый state перемещён в {backup.name}; LIVE заблокирован",
                    "meta": {"error": str(exc)},
                }
            )
            return state

    @staticmethod
    def _migrate_campaign(campaign: dict[str, Any]) -> None:
        campaign.setdefault("entryOidMap", {})
        campaign.setdefault("targetOidMap", {})
        campaign.setdefault("entryCloidMap", {})
        campaign.setdefault("targetCloidMap", {})
        campaign.setdefault("fallbackTargetOid", None)
        campaign.setdefault("fallbackTargetCloid", None)
        campaign.setdefault("managedNetSize", 0.0)
        campaign.setdefault("actualPositionSize", 0.0)
        campaign.setdefault("abortAfterClose", False)
        campaign.setdefault("autoRearmBlocked", False)
        campaign.setdefault("seenFills", [])
        campaign.setdefault("unknownSeenFills", [])
        campaign.setdefault("fillCursorMs", max(0, int(campaign.get("createdMs") or 0) - 60_000))
        campaign.setdefault("recoveryReason", None)
        campaign.setdefault("recoveryZeroConfirmations", 0)
        for level in campaign.get("levels", []):
            if level.get("oid"):
                campaign["entryOidMap"].setdefault(str(level["oid"]), int(level["index"]))
            if level.get("entryCloid"):
                campaign["entryCloidMap"].setdefault(str(level["entryCloid"]), int(level["index"]))
            level.setdefault("tpOid", None)
            level.setdefault("entryCloid", None)
            level.setdefault("targetCloid", None)
            if level.get("tpOid"):
                campaign["targetOidMap"].setdefault(str(level["tpOid"]), int(level["index"]))
            if level.get("targetCloid"):
                campaign["targetCloidMap"].setdefault(str(level["targetCloid"]), int(level["index"]))

    def _save_locked(self) -> None:
        tmp = self.state_path.with_suffix(".tmp")
        payload = json.dumps(self.state, ensure_ascii=False, indent=2)
        with tmp.open("w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            tmp.chmod(0o600)
        except OSError:
            pass
        os.replace(tmp, self.state_path)
        try:
            self.state_path.chmod(0o600)
        except OSError:
            pass
        try:
            directory_fd = os.open(self.state_path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass

    def _event_locked(self, event_type: str, message: str, **meta: Any) -> None:
        events = self.state.setdefault("events", [])
        events.append({"time": now_iso(), "type": event_type, "message": message, "meta": meta})
        del events[:-500]

    def _set_safe_mode_locked(self, reason: str) -> None:
        system = self.state.setdefault("system", {})
        system["safeMode"] = True
        system["safeModeReason"] = reason

    @staticmethod
    def _coin(value: str) -> str:
        coin = value.upper().replace("USDT", "").replace("USD", "")
        if coin not in SUPPORTED_COINS:
            raise LiveEngineError(f"Поддерживаются только BTC, ETH и SOL: {value}")
        return coin

    def _campaign_locked(self, coin: str) -> dict[str, Any] | None:
        return self.state.get("campaigns", {}).get(self._coin(coin))

    def _active_campaign_locked(self, coin: str) -> dict[str, Any] | None:
        campaign = self._campaign_locked(coin)
        return campaign if campaign and campaign.get("status") in ACTIVE_STATUSES else None

    def _active_campaigns_locked(self) -> list[dict[str, Any]]:
        return [
            campaign
            for campaign in self.state.get("campaigns", {}).values()
            if campaign.get("status") in ACTIVE_STATUSES
        ]

    def _size_tolerance(self, coin: str) -> float:
        return 10 ** (-self.gateway.sz_decimals(coin)) / 2

    @staticmethod
    def _position_size(account: dict[str, Any], coin: str) -> float:
        position = account.get("positions", {}).get(coin)
        return float(position.get("size") or 0) if position else 0.0

    def preview(self, coin: str, galka_price: float) -> dict[str, Any]:
        coin = self._coin(coin)
        galka_price = float(galka_price)
        if not math.isfinite(galka_price) or galka_price <= 0:
            raise LiveEngineError("Цена GALKA должна быть конечным числом больше нуля")
        mid = self.gateway.mids().get(coin)
        if not mid:
            raise LiveEngineError(f"Нет текущей цены {coin}")
        if mid <= galka_price:
            raise LiveEngineError(
                f"Текущая цена {mid:g} уже не выше GALKA {galka_price:g}. Сетка должна ждать падения сверху."
            )
        levels = self.gateway.preview_ladder(coin, galka_price, self.config.total_notional)
        account = self.gateway.fresh_account_state()
        actual_notional = sum(level.notional for level in levels)
        return {
            "coin": coin,
            "galkaPrice": galka_price,
            "currentPrice": mid,
            "levels": [level.to_dict() for level in levels],
            "requestedNotional": self.config.total_notional,
            "actualNotional": actual_notional,
            "requiredMargin": actual_notional / self.config.leverage,
            "leverage": self.config.leverage,
            "isolated": self.config.isolated,
            "weightedAverage": weighted_average(levels),
            "estimatedPnlAtGalka": estimated_target_pnl(
                levels, galka_price, self.config.maker_fee_rate
            ),
            "estimatedPnlMakerMaker": estimated_target_pnl(
                levels, galka_price, self.config.maker_fee_rate
            ),
            "estimatedPnlMakerTaker": estimated_target_pnl_mixed(
                levels,
                galka_price,
                self.config.maker_fee_rate,
                self.config.taker_fee_rate,
            ),
            "makerFeeRate": self.config.maker_fee_rate,
            "takerFeeRate": self.config.taker_fee_rate,
            "accountValue": account["accountValue"],
            "withdrawable": account["withdrawable"],
            "liveEnabled": self.config.live_enabled,
        }

    def create_campaign(self, coin: str, galka_price: float, confirmation: str) -> dict[str, Any]:
        coin = self._coin(coin)
        if not self.config.live_enabled:
            raise LiveEngineError(
                "LIVE выключен в локальном config. Установи HL_LIVE_ENABLED=YES и правильную строку подтверждения."
            )
        if confirmation != "PLACE_REAL_ORDERS":
            raise LiveEngineError("Не подтверждена отправка реальных ордеров")

        with self.action_lock:
            with self.lock:
                system = self.state.get("system", {})
                if system.get("safeMode"):
                    raise LiveEngineError(f"SAFE MODE: {system.get('safeModeReason') or 'требуется сверка'}")
                if self.monitor_thread.ident is not None and not self.monitor_thread.is_alive():
                    self._set_safe_mode_locked("Фоновый LIVE-монитор остановлен")
                    self._save_locked()
                    raise LiveEngineError("SAFE MODE: фоновый LIVE-монитор остановлен")
                active_campaigns = self._active_campaigns_locked()
                if active_campaigns:
                    active_coin = active_campaigns[0].get("coin", "?")
                    raise LiveEngineError(
                        f"Уже активна GALKA {active_coin}. Hardened LIVE допускает только одну кампанию одновременно."
                    )

            preview = self.preview(coin, galka_price)
            account = self.gateway.fresh_account_state()
            all_orders = self.gateway.fresh_open_orders()
            existing_positions = {
                item: self._position_size(account, item)
                for item in SUPPORTED_COINS
                if abs(self._position_size(account, item)) > self._size_tolerance(item)
            }
            if existing_positions:
                details = ", ".join(f"{item} {size:g}" for item, size in sorted(existing_positions.items()))
                raise LiveEngineError(
                    f"На поддерживаемых рынках уже есть реальные позиции: {details}. Новая GALKA не создана."
                )
            supported_orders = [row for row in all_orders if row.get("coin") in SUPPORTED_COINS]
            if supported_orders:
                raise LiveEngineError(
                    f"На BTC/ETH/SOL уже есть {len(supported_orders)} открытых ордеров. Сначала убери их вручную."
                )
            allowed_margin = max(0.0, account["withdrawable"] * self.config.max_margin_fraction)
            if preview["requiredMargin"] > allowed_margin:
                raise LiveEngineError(
                    f"Риск-лимит маржи: нужно около ${preview['requiredMargin']:.2f}, "
                    f"разрешено не более ${allowed_margin:.2f} "
                    f"({self.config.max_margin_fraction:.0%} от свободных ${account['withdrawable']:.2f})."
                )

            self.gateway.set_leverage(coin)
            levels = [LadderLevel(**row) for row in preview["levels"]]
            campaign_id = f"HL-{coin}-{now_ms()}-{uuid.uuid4().hex[:6]}"
            campaign = self._new_campaign(campaign_id, coin, galka_price, preview, levels)
            with self.lock:
                # Re-check after network reads in case another request completed first.
                if self._active_campaigns_locked():
                    raise LiveEngineError("Другая LIVE-кампания успела стать активной")
                self.state.setdefault("campaigns", {})[coin] = campaign
                self._save_locked()

            try:
                for level_state, level in zip(campaign["levels"], levels):
                    pair = self.gateway.place_entry_with_target(
                        coin,
                        level,
                        float(galka_price),
                        level_state["entryCloid"],
                        level_state["targetCloid"],
                    )
                    with self.lock:
                        self._record_pair_locked(campaign, level_state, pair)
                        campaign["updatedAt"] = now_iso()
                        self._save_locked()

                open_orders = self.gateway.fresh_open_orders(coin)
                with self.lock:
                    self._register_delayed_orders(campaign, open_orders)
                entry_open = [row for row in open_orders if self._entry_owner(campaign, row) is not None]
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
                        f"{coin}: реальная GALKA {galka_price:g}, выставлено 8 лимиток с биржевыми TP",
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

    def _new_campaign(
        self,
        campaign_id: str,
        coin: str,
        galka_price: float,
        preview: dict[str, Any],
        levels: list[LadderLevel],
    ) -> dict[str, Any]:
        created_ms = now_ms()
        level_rows = []
        entry_cloid_map: dict[str, int] = {}
        target_cloid_map: dict[str, int] = {}
        for level in levels:
            entry_cloid = new_cloid()
            target_cloid = new_cloid()
            entry_cloid_map[entry_cloid] = level.index
            target_cloid_map[target_cloid] = level.index
            level_rows.append(
                {
                    **level.to_dict(),
                    "oid": None,
                    "tpOid": None,
                    "entryCloid": entry_cloid,
                    "targetCloid": target_cloid,
                    "status": "new",
                    "filledSize": 0.0,
                    "averageFillPrice": 0.0,
                }
            )
        return {
            "id": campaign_id,
            "coin": coin,
            "status": "placing",
            "galkaPrice": float(galka_price),
            "createdAt": now_iso(),
            "createdMs": created_ms,
            "updatedAt": now_iso(),
            "leverage": self.config.leverage,
            "isolated": self.config.isolated,
            "requestedNotional": self.config.total_notional,
            "actualNotional": preview["actualNotional"],
            "levels": level_rows,
            "entryOidMap": {},
            "targetOidMap": {},
            "entryCloidMap": entry_cloid_map,
            "targetCloidMap": target_cloid_map,
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
            "autoRearmBlocked": False,
            "recoveryReason": None,
            "recoveryZeroConfirmations": 0,
            "lastError": None,
        }

    @staticmethod
    def _record_pair_locked(campaign: dict[str, Any], level_state: dict[str, Any], pair: Any) -> None:
        level_state["oid"] = pair.entry.oid or None
        level_state["tpOid"] = pair.target.oid or None
        level_state["status"] = pair.entry.status
        if pair.entry.oid:
            campaign["entryOidMap"][str(pair.entry.oid)] = int(level_state["index"])
        if pair.target.oid:
            campaign["targetOidMap"][str(pair.target.oid)] = int(level_state["index"])

    def _creation_failure(self, campaign: dict[str, Any], error: Exception) -> None:
        account: dict[str, Any] | None = None
        open_orders: list[dict[str, Any]] = []
        try:
            account = self.gateway.fresh_account_state()
            open_orders = self.gateway.fresh_open_orders(campaign["coin"])
        except Exception:
            pass
        actual = self._position_size(account or {}, campaign["coin"])
        self._enter_recovery(campaign, f"Ошибка создания: {error}", actual, open_orders)

    def cancel_waiting_campaign(self, coin: str) -> dict[str, Any]:
        coin = self._coin(coin)
        with self.action_lock:
            with self.lock:
                campaign = self._active_campaign_locked(coin)
                if not campaign:
                    raise LiveEngineError(f"Для {coin} нет активной GALKA")
            self._sync_campaign(campaign)
            with self.lock:
                if campaign.get("status") not in ACTIVE_STATUSES:
                    raise LiveEngineError(
                        f"Кампания уже завершила переход в статус {campaign.get('status')}"
                    )
            account = self.gateway.fresh_account_state()
            open_orders = self.gateway.fresh_open_orders(coin)
            actual = self._position_size(account, coin)
            tolerance = self._size_tolerance(coin)
            if abs(actual) > tolerance:
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
                if campaign.get("status") == RECOVERY_STATUS:
                    raise LiveEngineError("Кампания находится в recovery; используй аварийное закрытие или сверку")
                campaign["status"] = "canceling"
                campaign["updatedAt"] = now_iso()
                self._save_locked()

            try:
                self._cancel_owned_orders(campaign, open_orders=open_orders)
                ok, actual, remaining = self._confirm_flat_and_clean(campaign, reads=4)
                if not ok:
                    self._enter_recovery(
                        campaign,
                        "Отмена не подтверждена фактическим состоянием биржи",
                        actual,
                        remaining,
                    )
                    raise LiveEngineError("Биржа не подтвердила безопасную отмену; включён recovery")
            except Exception as exc:
                if campaign.get("status") != RECOVERY_STATUS:
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
                self._event_locked("live", f"{coin}: GALKA отменена без позиции", campaignId=campaign["id"])
                self._save_locked()
                return deepcopy(campaign)

    def emergency_close(self, coin: str, confirmation: str) -> dict[str, Any]:
        coin = self._coin(coin)
        if confirmation != "EMERGENCY_CLOSE_REAL_POSITION":
            raise LiveEngineError("Не подтверждено аварийное закрытие")
        with self.action_lock:
            with self.lock:
                campaign = self._active_campaign_locked(coin)
                if campaign:
                    campaign["status"] = "emergency"
                    campaign["autoRearmBlocked"] = True
                    campaign["updatedAt"] = now_iso()
                    self._save_locked()

            open_orders = self.gateway.fresh_open_orders(coin)
            cancel_error: Exception | None = None
            try:
                if campaign:
                    self._cancel_owned_orders(campaign, open_orders=open_orders)
                else:
                    self.gateway.cancel_oids(coin, [row["oid"] for row in open_orders])
            except Exception as exc:
                cancel_error = exc

            close_error: Exception | None = None
            for attempt in range(3):
                account = self.gateway.fresh_account_state()
                actual = self._position_size(account, coin)
                if abs(actual) <= self._size_tolerance(coin):
                    break
                try:
                    self.gateway.emergency_market_close(coin, new_cloid())
                except Exception as exc:
                    close_error = exc
                time.sleep(0.45 * (attempt + 1))

            success = False
            final_actual = 0.0
            final_orders: list[dict[str, Any]] = []
            for _ in range(8):
                account = self.gateway.fresh_account_state()
                final_actual = self._position_size(account, coin)
                final_orders = self.gateway.fresh_open_orders(coin)
                if abs(final_actual) <= self._size_tolerance(coin):
                    # Position is flat. Remove any still-owned targets/entries.
                    try:
                        if campaign:
                            self._cancel_owned_orders(campaign, open_orders=final_orders)
                        else:
                            if final_orders:
                                self.gateway.cancel_oids(coin, [row["oid"] for row in final_orders])
                        final_orders = self.gateway.fresh_open_orders(coin)
                    except Exception as exc:
                        cancel_error = exc
                    owned_remaining = (
                        self._owned_open_orders(campaign, final_orders) if campaign else final_orders
                    )
                    if not owned_remaining:
                        success = True
                        break
                time.sleep(0.40)

            if not success:
                reason = (
                    f"Аварийное закрытие не подтверждено: position={final_actual:g}; "
                    f"cancel={cancel_error}; close={close_error}"
                )
                if campaign:
                    self._enter_recovery(campaign, reason, final_actual, final_orders)
                with self.lock:
                    self._set_safe_mode_locked(reason)
                    self._event_locked("risk", f"{coin}: аварийное закрытие НЕ подтверждено")
                    self._save_locked()
                raise LiveEngineError(reason)

            with self.lock:
                self._set_safe_mode_locked(
                    f"После аварийного закрытия {coin} требуется ручная сверка"
                )
                if campaign:
                    campaign["status"] = "emergency_closed"
                    campaign["managedNetSize"] = 0.0
                    campaign["actualPositionSize"] = 0.0
                    campaign["completedAt"] = now_iso()
                    campaign["updatedAt"] = now_iso()
                self._event_locked(
                    "risk",
                    f"{coin}: позиция фактически закрыта, ордера удалены; включён SAFE MODE",
                    campaignId=campaign.get("id") if campaign else None,
                )
                self._save_locked()
                return deepcopy(campaign) if campaign else {
                    "coin": coin,
                    "status": "emergency_closed",
                    "completedAt": now_iso(),
                }

    def reconcile_system(self, confirmation: str) -> dict[str, Any]:
        if confirmation != "RECONCILE_LOCAL_STATE":
            raise LiveEngineError("Не подтверждена ручная сверка")
        with self.action_lock:
            for coin in sorted(SUPPORTED_COINS):
                with self.lock:
                    campaign = self._active_campaign_locked(coin)
                if campaign:
                    try:
                        self._sync_campaign(campaign)
                    except Exception as exc:
                        with self.lock:
                            campaign["lastError"] = str(exc)
            account = self.gateway.fresh_account_state()
            all_orders = self.gateway.fresh_open_orders()
            risks: list[str] = []
            with self.lock:
                for coin in sorted(SUPPORTED_COINS):
                    campaign = self._active_campaign_locked(coin)
                    actual = self._position_size(account, coin)
                    coin_orders = [row for row in all_orders if row.get("coin") == coin]
                    if campaign and campaign.get("status") == RECOVERY_STATUS:
                        risks.append(f"{coin}: campaign recovery")
                    elif not campaign:
                        if abs(actual) > self._size_tolerance(coin):
                            risks.append(f"{coin}: orphan position {actual:g}")
                        if coin_orders:
                            risks.append(f"{coin}: {len(coin_orders)} orphan orders")
                    elif self._foreign_open_orders(campaign, coin_orders):
                        risks.append(f"{coin}: foreign orders")

                system = self.state.setdefault("system", {})
                system["lastReconcileAt"] = now_iso()
                if risks:
                    system["safeMode"] = True
                    system["safeModeReason"] = "; ".join(risks)
                    self._event_locked("risk", "Сверка обнаружила риск: " + "; ".join(risks))
                else:
                    system["safeMode"] = False
                    system["safeModeReason"] = None
                    system["stateCorrupt"] = False
                    self._event_locked("live", "Сверка завершена: локальное состояние и биржа чистые")
                self._save_locked()
                return {"safeMode": system["safeMode"], "risks": risks}

    def status(self) -> dict[str, Any]:
        account = self.gateway.account_state()
        mids = self.gateway.mids()
        with self.lock:
            system = deepcopy(self.state.get("system", {}))
            system["monitorStarted"] = self.monitor_thread.ident is not None
            system["monitorAlive"] = self.monitor_thread.is_alive()
            return {
                "configured": True,
                "liveEnabled": self.config.live_enabled,
                "network": self.config.network_name,
                "account": self.config.masked_address,
                "agent": f"{self.gateway.agent_address[:6]}…{self.gateway.agent_address[-4:]}",
                "leverage": self.config.leverage,
                "isolated": self.config.isolated,
                "totalNotional": self.config.total_notional,
                "maxMarginFraction": self.config.max_margin_fraction,
                "system": system,
                "accountState": account,
                "mids": mids,
                "campaigns": deepcopy(self.state.get("campaigns", {})),
                "events": deepcopy(self.state.get("events", [])[-100:]),
                "serverTime": now_ms(),
            }

    def candles(self, coin: str, interval: str, limit: int) -> list[dict[str, Any]]:
        return self.gateway.candles(self._coin(coin), interval, limit)

    @staticmethod
    def _fill_key(fill: dict[str, Any]) -> str:
        return ":".join(str(fill.get(key, "")) for key in ("hash", "oid", "time", "side", "size", "price"))

    def _entry_owner(self, campaign: dict[str, Any], row: dict[str, Any]) -> int | None:
        oid = str(int(row.get("oid") or 0))
        cloid = str(row.get("cloid") or "")
        if oid in campaign.get("entryOidMap", {}):
            return int(campaign["entryOidMap"][oid])
        if cloid and cloid in campaign.get("entryCloidMap", {}):
            return int(campaign["entryCloidMap"][cloid])
        return None

    def _target_owner(self, campaign: dict[str, Any], row: dict[str, Any]) -> int | None:
        oid = str(int(row.get("oid") or 0))
        cloid = str(row.get("cloid") or "")
        if oid in campaign.get("targetOidMap", {}):
            return int(campaign["targetOidMap"][oid])
        if cloid and cloid in campaign.get("targetCloidMap", {}):
            return int(campaign["targetCloidMap"][cloid])
        return None

    def _register_delayed_orders(self, campaign: dict[str, Any], open_orders: list[dict[str, Any]]) -> None:
        for row in open_orders:
            oid = int(row.get("oid") or 0)
            if oid <= 0:
                continue
            entry_level = self._entry_owner(campaign, row)
            target_level = self._target_owner(campaign, row)
            if entry_level is not None:
                campaign.setdefault("entryOidMap", {})[str(oid)] = entry_level
                level = next((x for x in campaign.get("levels", []) if int(x["index"]) == entry_level), None)
                if level and not level.get("oid"):
                    level["oid"] = oid
            if target_level is not None:
                campaign.setdefault("targetOidMap", {})[str(oid)] = target_level
                if target_level > 0:
                    level = next((x for x in campaign.get("levels", []) if int(x["index"]) == target_level), None)
                    if level and not level.get("tpOid"):
                        level["tpOid"] = oid

    @staticmethod
    def _owner_from_maps(
        fill: dict[str, Any],
        entry_oid_map: dict[str, Any],
        target_oid_map: dict[str, Any],
        entry_cloid_map: dict[str, Any],
        target_cloid_map: dict[str, Any],
    ) -> tuple[str, int] | None:
        oid = int(fill.get("oid") or 0)
        cloid = str(fill.get("cloid") or "")
        if oid and str(oid) in entry_oid_map:
            return "entry", int(entry_oid_map[str(oid)])
        if oid and str(oid) in target_oid_map:
            return "target", int(target_oid_map[str(oid)])
        if cloid and cloid in entry_cloid_map:
            return "entry", int(entry_cloid_map[cloid])
        if cloid and cloid in target_cloid_map:
            return "target", int(target_cloid_map[cloid])
        return None

    def _prepare_fill_owners(
        self,
        campaign: dict[str, Any],
        fills: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Resolve delayed OID ownership without holding the state lock over I/O."""
        with self.lock:
            entry_oid_map = dict(campaign.get("entryOidMap", {}))
            target_oid_map = dict(campaign.get("targetOidMap", {}))
            entry_cloid_map = dict(campaign.get("entryCloidMap", {}))
            target_cloid_map = dict(campaign.get("targetCloidMap", {}))

        prepared: list[dict[str, Any]] = []
        for raw in fills:
            fill = dict(raw)
            owner = self._owner_from_maps(
                fill,
                entry_oid_map,
                target_oid_map,
                entry_cloid_map,
                target_cloid_map,
            )
            resolved_cloid = ""
            oid = int(fill.get("oid") or 0)
            if owner is None and oid and hasattr(self.gateway, "order_status"):
                try:
                    order = self.gateway.order_status(oid)
                except Exception:
                    order = None
                if order:
                    resolved_cloid = str(order.get("cloid") or "")
                    probe = {**fill, "cloid": resolved_cloid}
                    owner = self._owner_from_maps(
                        probe,
                        entry_oid_map,
                        target_oid_map,
                        entry_cloid_map,
                        target_cloid_map,
                    )
                    if owner and owner[0] == "entry":
                        entry_oid_map[str(oid)] = owner[1]
                    elif owner and owner[0] == "target":
                        target_oid_map[str(oid)] = owner[1]
            if owner:
                fill["_ownerKind"] = owner[0]
                fill["_ownerLevel"] = owner[1]
            if resolved_cloid:
                fill["_resolvedCloid"] = resolved_cloid
            prepared.append(fill)
        return prepared

    def _apply_new_fills(self, campaign: dict[str, Any], fills: list[dict[str, Any]]) -> None:
        seen_list = list(campaign.get("seenFills", []))
        seen = set(seen_list)
        unknown = set(campaign.get("unknownSeenFills", []))
        levels_by_index = {int(level["index"]): level for level in campaign.get("levels", [])}
        max_time = int(campaign.get("fillCursorMs") or 0)

        for fill in sorted(fills, key=lambda row: int(row.get("time") or 0)):
            max_time = max(max_time, int(fill.get("time") or 0))
            key = self._fill_key(fill)
            if key in seen:
                continue
            kind = fill.get("_ownerKind")
            level_index = int(fill.get("_ownerLevel") or 0)
            owner = (str(kind), level_index) if kind in {"entry", "target"} else None
            oid = int(fill.get("oid") or 0)
            resolved_cloid = str(fill.get("_resolvedCloid") or fill.get("cloid") or "")
            if owner and oid:
                if owner[0] == "entry":
                    campaign.setdefault("entryOidMap", {})[str(oid)] = owner[1]
                else:
                    campaign.setdefault("targetOidMap", {})[str(oid)] = owner[1]
            if resolved_cloid and not fill.get("cloid"):
                fill["cloid"] = resolved_cloid

            size = float(fill.get("size") or 0)
            fee = float(fill.get("fee") or 0)
            if owner and owner[0] == "entry" and fill.get("side") == "B":
                index = owner[1]
                level = levels_by_index.get(index)
                if level:
                    old_size = float(level.get("filledSize") or 0)
                    total_size = old_size + size
                    old_notional = old_size * float(level.get("averageFillPrice") or 0)
                    add_notional = size * float(fill.get("price") or 0)
                    level["filledSize"] = total_size
                    level["averageFillPrice"] = (
                        (old_notional + add_notional) / total_size if total_size else 0
                    )
                    level["status"] = (
                        "partial" if total_size + 1e-12 < float(level["size"]) else "filled"
                    )
                    campaign["cycleDeepest"] = max(int(campaign.get("cycleDeepest") or 0), index)
                    campaign["hadPosition"] = True
                    campaign["managedNetSize"] = float(campaign.get("managedNetSize") or 0) + size
                    campaign["cycleFees"] = float(campaign.get("cycleFees") or 0) + fee
                    self._event_locked(
                        "fill",
                        f"{campaign['coin']}: L{index} исполнена на {size:g}",
                        campaignId=campaign["id"],
                        price=fill.get("price"),
                    )
            elif owner and owner[0] == "target" and fill.get("side") == "A":
                campaign["managedNetSize"] = max(
                    0.0, float(campaign.get("managedNetSize") or 0) - size
                )
                campaign["cycleClosedPnl"] = float(campaign.get("cycleClosedPnl") or 0) + float(
                    fill.get("closedPnl") or 0
                )
                campaign["cycleFees"] = float(campaign.get("cycleFees") or 0) + fee
            else:
                if key not in unknown:
                    unknown.add(key)
                    # Any manual/foreign fill on the same coin makes a later
                    # automatic L1 rearm unsafe, even if two foreign fills happen
                    # to net to zero before the next venue snapshot.
                    if int(fill.get("time") or 0) >= int(campaign.get("createdMs") or 0) - 2_000:
                        campaign["autoRearmBlocked"] = True
                    self._event_locked(
                        "risk",
                        f"{campaign['coin']}: обнаружен fill, не принадлежащий GALKA; автоматический rearm будет заблокирован при расхождении",
                        campaignId=campaign["id"],
                        oid=fill.get("oid"),
                        price=fill.get("price"),
                        side=fill.get("side"),
                    )
            seen.add(key)
            seen_list.append(key)

        campaign["seenFills"] = seen_list[-4000:]
        campaign["unknownSeenFills"] = list(unknown)[-1000:]
        campaign["fillCursorMs"] = max(0, max_time - 2_000)

    def _sync_campaign(self, campaign: dict[str, Any]) -> None:
        coin = campaign["coin"]
        open_orders = self.gateway.fresh_open_orders(coin)
        account = self.gateway.fresh_account_state()
        cursor = max(0, int(campaign.get("fillCursorMs") or campaign["createdMs"] - 60_000))
        fills = [row for row in self.gateway.fills_since(cursor) if row.get("coin") == coin]
        prepared_fills = self._prepare_fill_owners(campaign, fills)

        with self.lock:
            self._register_delayed_orders(campaign, open_orders)
            self._apply_new_fills(campaign, prepared_fills)
            actual = self._position_size(account, coin)
            managed = float(campaign.get("managedNetSize") or 0)
            campaign["actualPositionSize"] = actual
            campaign["updatedAt"] = now_iso()
            if len(fills) >= 1990:
                campaign["autoRearmBlocked"] = True
                campaign["lastError"] = "Окно fills близко к лимиту API; требуется recovery"
            foreign = self._foreign_open_orders(campaign, open_orders)
            mismatch = abs(actual - managed) > self._size_tolerance(coin)
            short_position = actual < -self._size_tolerance(coin)
            recovery_reason = None
            if len(fills) >= 1990:
                recovery_reason = "История fills достигла лимита ответа API"
            elif foreign:
                recovery_reason = f"Обнаружено посторонних ордеров: {len(foreign)}"
            elif short_position:
                recovery_reason = f"Неожиданная short-позиция {actual:g}"
            elif mismatch:
                recovery_reason = f"Расхождение позиции: биржа {actual:g}, GALKA {managed:g}"

        if recovery_reason and campaign.get("status") != RECOVERY_STATUS:
            self._enter_recovery(campaign, recovery_reason, actual, open_orders)
            return
        if campaign.get("status") == RECOVERY_STATUS:
            self._sync_recovery(campaign, actual, open_orders)
            return

        open_by_oid = {int(row["oid"]): row for row in open_orders}
        with self.lock:
            for level in campaign.get("levels", []):
                oid = int(level["oid"]) if level.get("oid") else 0
                if oid and oid in open_by_oid:
                    level["status"] = "partial" if float(level.get("filledSize") or 0) > 0 else "resting"
                elif float(level.get("filledSize") or 0) >= float(level.get("size") or 0) - 1e-12:
                    level["status"] = "filled"

        tolerance = self._size_tolerance(coin)
        if actual > tolerance:
            with self.lock:
                campaign["hadPosition"] = True
                campaign["status"] = "open"
            self._ensure_target_coverage(campaign, open_orders, actual)
        elif campaign.get("hadPosition"):
            if float(campaign.get("managedNetSize") or 0) > tolerance:
                self._enter_recovery(
                    campaign,
                    "Биржа уже flat, но локальные owned fills не подтверждают закрытие",
                    actual,
                    open_orders,
                )
                return
            self._finish_cycle(campaign)
        with self.lock:
            campaign["lastError"] = None
            campaign["updatedAt"] = now_iso()
            self._save_locked()

    def _is_galka_target(self, campaign: dict[str, Any], order: dict[str, Any]) -> bool:
        if not order.get("reduceOnly") or order.get("side") != "A":
            return False
        galka = float(campaign["galkaPrice"])
        price = float(order.get("triggerPrice") or order.get("price") or 0)
        return abs(price - galka) <= max(1e-9, galka * 1e-7)

    def _ensure_target_coverage(
        self,
        campaign: dict[str, Any],
        open_orders: list[dict[str, Any]],
        actual_size: float,
    ) -> None:
        coin = campaign["coin"]
        tolerance = self._size_tolerance(coin)
        if actual_size <= tolerance:
            return
        with self.lock:
            self._register_delayed_orders(campaign, open_orders)
            fallback_oid = int(campaign.get("fallbackTargetOid") or 0)
            fallback_open: dict[str, Any] | None = None
            native_protected = 0.0
            malformed: list[int] = []
            for order in open_orders:
                owner = self._target_owner(campaign, order)
                if owner is None:
                    continue
                oid = int(order.get("oid") or 0)
                if not self._is_galka_target(campaign, order):
                    malformed.append(oid)
                    continue
                if oid == fallback_oid or owner == 0:
                    fallback_open = order
                else:
                    native_protected += float(order.get("size") or 0)
        if malformed:
            raise LiveEngineError(f"Owned target orders have wrong parameters: {malformed}")

        if native_protected + tolerance >= actual_size:
            if fallback_open:
                self._cancel_specific_and_verify(coin, [int(fallback_open["oid"])])
                with self.lock:
                    campaign["fallbackTargetOid"] = None
            with self.lock:
                if campaign.get("status") != RECOVERY_STATUS:
                    campaign["status"] = "closing"
                self._save_locked()
            return

        missing = actual_size - native_protected
        existing_oid = int(fallback_open["oid"]) if fallback_open else None
        existing_size = float(fallback_open.get("size") or 0) if fallback_open else 0.0
        if fallback_open and abs(existing_size - missing) <= tolerance:
            with self.lock:
                if campaign.get("status") != RECOVERY_STATUS:
                    campaign["status"] = "closing"
            return

        fallback_cloid = campaign.get("fallbackTargetCloid") or new_cloid()
        fallback = self.gateway.place_or_replace_target(
            coin,
            missing,
            float(campaign["galkaPrice"]),
            existing_oid,
            fallback_cloid,
        )
        with self.lock:
            if fallback.status == "filled":
                campaign["fallbackTargetOid"] = None
                campaign["fallbackTargetCloid"] = None
                self._event_locked(
                    "live",
                    f"{coin}: резервный target исполнился немедленно; состояние будет перепроверено",
                    campaignId=campaign["id"],
                    size=missing,
                    oid=fallback.oid,
                )
            else:
                campaign["fallbackTargetOid"] = fallback.oid
                campaign["fallbackTargetCloid"] = fallback_cloid
                campaign.setdefault("targetOidMap", {})[str(fallback.oid)] = 0
                campaign.setdefault("targetCloidMap", {})[fallback_cloid] = 0
                self._event_locked(
                    "risk",
                    f"{coin}: добавлена/обновлена резервная reduce-only лимитка на GALKA",
                    campaignId=campaign["id"],
                    size=missing,
                    oid=fallback.oid,
                )
            if campaign.get("status") != RECOVERY_STATUS:
                campaign["status"] = "closing"
            self._save_locked()

    def _enter_recovery(
        self,
        campaign: dict[str, Any],
        reason: str,
        actual_size: float,
        open_orders: list[dict[str, Any]],
    ) -> None:
        with self.lock:
            first = campaign.get("status") != RECOVERY_STATUS
            campaign["status"] = RECOVERY_STATUS
            campaign["autoRearmBlocked"] = True
            campaign["recoveryReason"] = reason
            campaign["lastError"] = reason
            campaign["actualPositionSize"] = actual_size
            campaign["recoveryZeroConfirmations"] = 0
            campaign["updatedAt"] = now_iso()
            self._set_safe_mode_locked(f"{campaign['coin']} recovery: {reason}")
            if first:
                self._event_locked(
                    "risk",
                    f"{campaign['coin']}: RECOVERY — {reason}",
                    campaignId=campaign["id"],
                )
            self._save_locked()

        try:
            self._cancel_owned_orders(campaign, open_orders=open_orders, entries_only=True)
        except Exception as exc:
            with self.lock:
                campaign["lastError"] = f"{reason}; cancel entries: {exc}"
                self._save_locked()
        if actual_size > self._size_tolerance(campaign["coin"]):
            try:
                fresh_orders = self.gateway.fresh_open_orders(campaign["coin"])
                self._ensure_target_coverage(campaign, fresh_orders, actual_size)
            except Exception as exc:
                with self.lock:
                    campaign["lastError"] = f"{reason}; target protection: {exc}"
                    self._event_locked(
                        "risk",
                        f"{campaign['coin']}: не удалось подтвердить защитный target; проверь биржу немедленно",
                        campaignId=campaign["id"],
                    )
                    self._save_locked()

    def _sync_recovery(
        self,
        campaign: dict[str, Any],
        actual_size: float,
        open_orders: list[dict[str, Any]],
    ) -> None:
        coin = campaign["coin"]
        tolerance = self._size_tolerance(coin)
        try:
            self._cancel_owned_orders(campaign, open_orders=open_orders, entries_only=True)
        except Exception as exc:
            with self.lock:
                campaign["lastError"] = f"Recovery cancel entries: {exc}"

        if actual_size > tolerance:
            with self.lock:
                campaign["recoveryZeroConfirmations"] = 0
            try:
                self._ensure_target_coverage(campaign, self.gateway.fresh_open_orders(coin), actual_size)
            except Exception as exc:
                with self.lock:
                    campaign["lastError"] = f"Recovery target: {exc}"
            with self.lock:
                campaign["updatedAt"] = now_iso()
                self._save_locked()
            return
        if actual_size < -tolerance:
            with self.lock:
                campaign["lastError"] = f"Recovery cannot manage short position {actual_size:g}"
                campaign["updatedAt"] = now_iso()
                self._save_locked()
            return

        # Flat: remove all owned orders, then require three independent clean syncs.
        try:
            self._cancel_owned_orders(campaign, open_orders=self.gateway.fresh_open_orders(coin))
        except Exception as exc:
            with self.lock:
                campaign["lastError"] = f"Recovery final cancel: {exc}"
                campaign["recoveryZeroConfirmations"] = 0
                self._save_locked()
            return
        remaining = self._owned_open_orders(campaign, self.gateway.fresh_open_orders(coin))
        with self.lock:
            if remaining:
                campaign["recoveryZeroConfirmations"] = 0
            else:
                campaign["recoveryZeroConfirmations"] = int(
                    campaign.get("recoveryZeroConfirmations") or 0
                ) + 1
            if campaign["recoveryZeroConfirmations"] >= 3:
                campaign["status"] = "recovery_closed"
                campaign["managedNetSize"] = 0.0
                campaign["actualPositionSize"] = 0.0
                campaign["completedAt"] = now_iso()
                self._event_locked(
                    "risk",
                    f"{coin}: recovery завершён flat; автоматический rearm не выполнялся",
                    campaignId=campaign["id"],
                )
            campaign["updatedAt"] = now_iso()
            self._save_locked()

    def _finish_cycle(self, campaign: dict[str, Any]) -> None:
        coin = campaign["coin"]
        deepest = int(campaign.get("cycleDeepest") or 0)
        net_cycle = float(campaign.get("cycleClosedPnl") or 0) - float(campaign.get("cycleFees") or 0)
        if campaign.get("autoRearmBlocked") or deepest <= 0:
            self._enter_recovery(
                campaign,
                "Цикл закрыт без доказанного owned TP; rearm запрещён",
                0.0,
                self.gateway.fresh_open_orders(coin),
            )
            return

        if campaign.get("abortAfterClose"):
            self._cancel_owned_orders(campaign)
            with self.lock:
                campaign["status"] = "error_closed"
                campaign["completedAt"] = now_iso()
                campaign["hadPosition"] = False
                campaign["finalClosedPnl"] = net_cycle
                campaign["managedNetSize"] = 0.0
                self._event_locked(
                    "risk",
                    f"{coin}: защищённая неполная кампания закрыта и остановлена",
                    campaignId=campaign["id"],
                    pnl=net_cycle,
                )
                self._save_locked()
            return

        if deepest == 1:
            # A limit entry can be partially filled and remain resting. Never add a
            # fresh L1 until every old L1 entry/target is gone and the venue has
            # independently confirmed that the position is still flat.
            try:
                self._cancel_level_orders(campaign, 1)
            except Exception as exc:
                latest_account = self.gateway.fresh_account_state()
                latest_orders = self.gateway.fresh_open_orders(coin)
                self._enter_recovery(
                    campaign,
                    f"Не удалось очистить старую L1 перед rearm: {exc}",
                    self._position_size(latest_account, coin),
                    latest_orders,
                )
                return
            flat, actual = self._confirm_flat(coin, reads=2)
            if not flat:
                self._enter_recovery(
                    campaign,
                    "Новая позиция появилась во время L1 rearm",
                    actual,
                    self.gateway.fresh_open_orders(coin),
                )
                return

            l1 = next(level for level in campaign["levels"] if int(level["index"]) == 1)
            entry_cloid = new_cloid()
            target_cloid = new_cloid()
            pair = self.gateway.place_entry_with_target(
                coin,
                LadderLevel(
                    index=int(l1["index"]),
                    depth_pct=float(l1["depth_pct"]),
                    weight=float(l1["weight"]),
                    price=float(l1["price"]),
                    size=float(l1["size"]),
                    notional=float(l1["notional"]),
                ),
                float(campaign["galkaPrice"]),
                entry_cloid,
                target_cloid,
            )
            with self.lock:
                l1.update(
                    {
                        "oid": pair.entry.oid or None,
                        "tpOid": pair.target.oid or None,
                        "entryCloid": entry_cloid,
                        "targetCloid": target_cloid,
                        "status": pair.entry.status,
                        "filledSize": 0.0,
                        "averageFillPrice": 0.0,
                    }
                )
                campaign["entryCloidMap"][entry_cloid] = 1
                campaign["targetCloidMap"][target_cloid] = 1
                if pair.entry.oid:
                    campaign["entryOidMap"][str(pair.entry.oid)] = 1
                if pair.target.oid:
                    campaign["targetOidMap"][str(pair.target.oid)] = 1
                campaign["l1Cycles"] = int(campaign.get("l1Cycles") or 0) + 1
                campaign["l1RealizedPnl"] = float(campaign.get("l1RealizedPnl") or 0) + net_cycle
                campaign["cycleClosedPnl"] = 0.0
                campaign["cycleFees"] = 0.0
                campaign["cycleDeepest"] = 0
                campaign["managedNetSize"] = 0.0
                campaign["actualPositionSize"] = 0.0
                campaign["hadPosition"] = False
                campaign["status"] = "waiting"
                self._event_locked(
                    "live",
                    f"{coin}: owned L1 TP закрыт на GALKA, L1 выставлена снова",
                    campaignId=campaign["id"],
                    cycle=campaign["l1Cycles"],
                    pnl=net_cycle,
                )
                self._save_locked()
            return

        self._cancel_owned_orders(campaign)
        ok, actual, remaining = self._confirm_flat_and_clean(campaign, reads=3)
        if not ok:
            self._enter_recovery(
                campaign,
                "Финальная отмена после L2+ не подтверждена",
                actual,
                remaining,
            )
            return
        with self.lock:
            campaign["status"] = "completed"
            campaign["completedAt"] = now_iso()
            campaign["hadPosition"] = False
            campaign["managedNetSize"] = 0.0
            campaign["actualPositionSize"] = 0.0
            campaign["finalClosedPnl"] = net_cycle
            self._event_locked(
                "live",
                f"{coin}: L{deepest} достигнута, позиция закрыта owned TP, кампания завершена",
                campaignId=campaign["id"],
                deepest=deepest,
                pnl=net_cycle,
            )
            self._save_locked()

    def _cancel_level_orders(self, campaign: dict[str, Any], level_index: int) -> None:
        rows = self.gateway.fresh_open_orders(campaign["coin"])
        selected = [
            row
            for row in rows
            if self._entry_owner(campaign, row) == level_index
            or self._target_owner(campaign, row) == level_index
        ]
        self._cancel_specific_and_verify(campaign["coin"], [row["oid"] for row in selected])

    def _confirm_flat(self, coin: str, reads: int = 2) -> tuple[bool, float]:
        clean = 0
        actual = 0.0
        for _ in range(reads + 2):
            actual = self._position_size(self.gateway.fresh_account_state(), coin)
            if abs(actual) <= self._size_tolerance(coin):
                clean += 1
                if clean >= reads:
                    return True, actual
            else:
                return False, actual
            time.sleep(0.20)
        return False, actual

    def _owned_open_orders(
        self,
        campaign: dict[str, Any] | None,
        open_orders: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not campaign:
            return []
        return [
            row
            for row in open_orders
            if self._entry_owner(campaign, row) is not None
            or self._target_owner(campaign, row) is not None
        ]

    def _foreign_open_orders(
        self,
        campaign: dict[str, Any],
        open_orders: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return [
            row
            for row in open_orders
            if self._entry_owner(campaign, row) is None
            and self._target_owner(campaign, row) is None
        ]

    def _cancel_specific_and_verify(self, coin: str, oids: list[int]) -> None:
        unique = sorted({int(oid) for oid in oids if int(oid) > 0})
        if not unique:
            return
        try:
            self.gateway.cancel_oids(coin, unique)
        except Exception:
            remaining = {row["oid"] for row in self.gateway.fresh_open_orders(coin)} & set(unique)
            if remaining:
                raise
        remaining = {row["oid"] for row in self.gateway.fresh_open_orders(coin)} & set(unique)
        if remaining:
            raise LiveEngineError(f"Биржа оставила ордера открытыми: {sorted(remaining)}")

    def _cancel_owned_orders(
        self,
        campaign: dict[str, Any],
        open_orders: list[dict[str, Any]] | None = None,
        entries_only: bool = False,
    ) -> None:
        rows = open_orders if open_orders is not None else self.gateway.fresh_open_orders(campaign["coin"])
        owned = self._owned_open_orders(campaign, rows)
        if entries_only:
            owned = [row for row in owned if self._entry_owner(campaign, row) is not None]
        self._cancel_specific_and_verify(campaign["coin"], [row["oid"] for row in owned])

    def _confirm_flat_and_clean(
        self,
        campaign: dict[str, Any],
        reads: int = 3,
    ) -> tuple[bool, float, list[dict[str, Any]]]:
        coin = campaign["coin"]
        clean_count = 0
        last_actual = 0.0
        last_orders: list[dict[str, Any]] = []
        for _ in range(reads + 2):
            account = self.gateway.fresh_account_state()
            last_actual = self._position_size(account, coin)
            last_orders = self.gateway.fresh_open_orders(coin)
            owned = self._owned_open_orders(campaign, last_orders)
            if abs(last_actual) <= self._size_tolerance(coin) and not owned:
                clean_count += 1
                if clean_count >= reads:
                    return True, last_actual, last_orders
            else:
                clean_count = 0
                if abs(last_actual) > self._size_tolerance(coin):
                    return False, last_actual, last_orders
            time.sleep(0.25)
        return False, last_actual, last_orders

    def _scan_global_risks(self) -> list[str]:
        """Detect positions/orders that are not owned by an active campaign."""
        account = self.gateway.fresh_account_state()
        orders = self.gateway.fresh_open_orders()
        risks: list[str] = []
        with self.lock:
            for coin in sorted(SUPPORTED_COINS):
                campaign = self._active_campaign_locked(coin)
                actual = self._position_size(account, coin)
                coin_orders = [row for row in orders if row.get("coin") == coin]
                if not campaign:
                    if abs(actual) > self._size_tolerance(coin):
                        risks.append(f"{coin}: orphan position {actual:g}")
                    if coin_orders:
                        risks.append(f"{coin}: {len(coin_orders)} orphan orders")
                elif self._foreign_open_orders(campaign, coin_orders):
                    risks.append(f"{coin}: foreign orders")

            system = self.state.setdefault("system", {})
            system["lastGlobalCheckAt"] = now_iso()
            if risks:
                reason = "; ".join(risks)
                changed = not system.get("safeMode") or system.get("safeModeReason") != reason
                self._set_safe_mode_locked(reason)
                if changed:
                    self._event_locked("risk", "Глобальная сверка обнаружила риск: " + reason)
            self._save_locked()
        return risks

    def _initial_reconcile(self) -> None:
        with self.lock:
            campaigns = [
                campaign
                for campaign in self.state.get("campaigns", {}).values()
                if campaign.get("status") in ACTIVE_STATUSES
            ]
        for campaign in campaigns:
            try:
                self._sync_campaign(campaign)
            except Exception as exc:
                with self.lock:
                    campaign["lastError"] = str(exc)
                    self._set_safe_mode_locked(f"{campaign['coin']} startup sync: {exc}")
                    self._event_locked("risk", f"{campaign['coin']}: ошибка стартовой сверки")
                    self._save_locked()

        account = self.gateway.fresh_account_state()
        orders = self.gateway.fresh_open_orders()
        risks: list[str] = []
        with self.lock:
            for coin in sorted(SUPPORTED_COINS):
                campaign = self._active_campaign_locked(coin)
                coin_orders = [row for row in orders if row.get("coin") == coin]
                actual = self._position_size(account, coin)
                if not campaign:
                    if abs(actual) > self._size_tolerance(coin):
                        risks.append(f"{coin}: orphan position {actual:g}")
                    if coin_orders:
                        risks.append(f"{coin}: {len(coin_orders)} orphan orders")
                elif self._foreign_open_orders(campaign, coin_orders):
                    risks.append(f"{coin}: foreign orders")
            if risks:
                self._set_safe_mode_locked("; ".join(risks))
                self._event_locked("risk", "Стартовая сверка обнаружила риск: " + "; ".join(risks))
            self.state.setdefault("system", {})["lastReconcileAt"] = now_iso()
            self._save_locked()

    def _monitor_loop(self) -> None:
        next_global_check = 0.0
        while not self.stop_event.wait(self.config.monitor_interval):
            if not self.config.live_enabled:
                continue
            if not self.action_lock.acquire(timeout=0.05):
                continue
            try:
                with self.lock:
                    self.state.setdefault("system", {})["monitorHeartbeatAt"] = now_iso()
                    campaigns = [
                        campaign
                        for campaign in self.state.get("campaigns", {}).values()
                        if campaign.get("status") in ACTIVE_STATUSES
                    ]
                for campaign in campaigns:
                    try:
                        self._sync_campaign(campaign)
                    except Exception as exc:
                        with self.lock:
                            campaign["lastError"] = str(exc)
                            campaign["updatedAt"] = now_iso()
                            self._set_safe_mode_locked(f"{campaign['coin']} sync error: {exc}")
                            self._event_locked(
                                "error",
                                f"{campaign['coin']}: синхронизация LIVE: {exc}",
                                campaignId=campaign.get("id"),
                            )
                            self._save_locked()

                monotonic_now = time.monotonic()
                if monotonic_now >= next_global_check:
                    try:
                        self._scan_global_risks()
                    except Exception as exc:
                        with self.lock:
                            self._set_safe_mode_locked(f"Глобальная сверка не выполнена: {exc}")
                            self._event_locked("error", f"Глобальная сверка LIVE: {exc}")
                            self._save_locked()
                    next_global_check = monotonic_now + self.config.global_check_interval
            finally:
                self.action_lock.release()

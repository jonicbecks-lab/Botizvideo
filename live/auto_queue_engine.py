from __future__ import annotations

import math
import threading
import time
import uuid
from copy import deepcopy
from typing import Any

from .engine import ACTIVE_STATUSES, LiveEngineError, now_iso, now_ms
from .hyperliquid_gateway import SUPPORTED_COINS
from .research_engine import ResearchCompatibleGalkaLiveEngine


AUTO_SOURCE_STATUSES = {"placing", "waiting", "open", "closing"}
AUTO_WATCH_STATUSES = {"queued", "paused"}
AUTO_QUEUE_CONFIRMATION = "QUEUE_REAL_GALKA"
AUTO_ACTIVATE_CONFIRMATION = "ACTIVATE_QUEUED_GALKA"
AUTO_DELETE_CONFIRMATION = "DELETE_QUEUED_GALKA"


class AutoQueueGalkaLiveEngine(ResearchCompatibleGalkaLiveEngine):
    """Adds one persisted next-GALKA queue per coin without changing GALKA execution.

    A queued GALKA does not place any exchange order. It becomes eligible only after
    the source campaign reaches the normal ``completed`` state. Any observed touch
    of the queued level after ``queuedMs`` permanently invalidates that queued item.

    Manual cancel, manual near-market exit, recovery and emergency paths never
    auto-activate the next GALKA; they pause it for explicit user action instead.
    """

    def __init__(self, config: Any, gateway: Any):
        super().__init__(config, gateway)
        with self.lock:
            self.state.setdefault("queuedGalkas", {})
        self._auto_queue_thread = threading.Thread(
            target=self._auto_queue_loop,
            name="galka-auto-queue",
            daemon=True,
        )

    def start(self) -> None:
        super().start()
        self._auto_queue_thread.start()

    def stop(self) -> None:
        try:
            super().stop()
        finally:
            if self._auto_queue_thread.is_alive():
                self._auto_queue_thread.join(timeout=3)

    def _queue_locked(self, coin: str) -> dict[str, Any] | None:
        row = self.state.setdefault("queuedGalkas", {}).get(coin)
        return row if isinstance(row, dict) else None

    def queued_galka_status(self, coin: str) -> dict[str, Any] | None:
        normalized = self._coin(coin)
        with self.lock:
            row = self._queue_locked(normalized)
            return deepcopy(row) if row else None

    def queue_next_galka(
        self,
        coin: str,
        galka_price: float,
        confirmation: str,
    ) -> dict[str, Any]:
        normalized = self._coin(coin)
        self._require_live_writes()
        if confirmation != AUTO_QUEUE_CONFIRMATION:
            raise LiveEngineError("Не подтверждена постановка следующей GALKA в AUTO")

        price = float(galka_price)
        if not math.isfinite(price) or price <= 0:
            raise LiveEngineError("Цена AUTO GALKA должна быть конечным числом больше нуля")

        with self.lock:
            system = self.state.get("system", {})
            if system.get("safeMode"):
                raise LiveEngineError(
                    f"SAFE MODE: {system.get('safeModeReason') or 'AUTO постановка заблокирована'}"
                )
            source = self._active_campaign_locked(normalized)
            if not source or source.get("status") not in AUTO_SOURCE_STATUSES:
                raise LiveEngineError(
                    "AUTO GALKA ставится в очередь только пока текущая GALKA работает в нормальном режиме"
                )
            existing = self._queue_locked(normalized)
            if existing and existing.get("status") == "activating":
                raise LiveEngineError("AUTO GALKA уже активируется; дождись результата")
            source_id = str(source.get("id") or "")

        mid = float(self.gateway.mids().get(normalized) or 0)
        if mid <= 0:
            raise LiveEngineError(f"Нет текущей цены {normalized}")
        if mid <= price:
            raise LiveEngineError(
                f"Текущая цена {mid:g} уже коснулась/ниже AUTO GALKA {price:g}; такой уровень ставить в очередь поздно"
            )

        queued_ms = now_ms()
        row = {
            "id": f"AUTO-{normalized}-{queued_ms}-{uuid.uuid4().hex[:6]}",
            "coin": normalized,
            "galkaPrice": price,
            "status": "queued",
            "queuedAt": now_iso(),
            "queuedMs": queued_ms,
            "sourceCampaignId": source_id,
            "sourceCampaignStatusAtQueue": source.get("status"),
            "midAtQueue": mid,
            "lastCheckedAt": now_iso(),
            "invalidatedAt": None,
            "invalidatedReason": None,
            "pausedAt": None,
            "pausedReason": None,
            "activatedAt": None,
            "activatedCampaignId": None,
            "lastError": None,
            "historyValidation": None,
        }
        with self.lock:
            previous = self._queue_locked(normalized)
            self.state.setdefault("queuedGalkas", {})[normalized] = row
            message = f"{normalized}: AUTO GALKA {price:g} поставлена в очередь"
            if previous:
                message += f" (предыдущая {previous.get('galkaPrice')} заменена)"
            self._event_locked(
                "live",
                message,
                autoQueue=True,
                queueId=row["id"],
                sourceCampaignId=source_id,
                galkaPrice=price,
            )
            self._save_locked()
            return deepcopy(row)

    def delete_queued_galka(self, coin: str, confirmation: str) -> dict[str, Any]:
        normalized = self._coin(coin)
        if confirmation != AUTO_DELETE_CONFIRMATION:
            raise LiveEngineError("Не подтверждено удаление AUTO GALKA")
        with self.lock:
            row = self._queue_locked(normalized)
            if not row:
                raise LiveEngineError(f"Для {normalized} нет AUTO GALKA")
            if row.get("status") == "activating":
                raise LiveEngineError("AUTO GALKA уже активируется и не может быть удалена")
            removed = deepcopy(row)
            self.state.setdefault("queuedGalkas", {}).pop(normalized, None)
            self._event_locked(
                "live",
                f"{normalized}: AUTO GALKA {removed.get('galkaPrice')} удалена пользователем",
                autoQueue=True,
                queueId=removed.get("id"),
            )
            self._save_locked()
            return removed

    def activate_queued_galka(self, coin: str, confirmation: str) -> dict[str, Any]:
        normalized = self._coin(coin)
        self._require_live_writes()
        if confirmation != AUTO_ACTIVATE_CONFIRMATION:
            raise LiveEngineError("Не подтверждена ручная активация AUTO GALKA")
        with self.lock:
            row = self._queue_locked(normalized)
            if not row:
                raise LiveEngineError(f"Для {normalized} нет AUTO GALKA")
            if row.get("status") != "paused":
                raise LiveEngineError(
                    f"Ручная активация доступна только для PAUSED AUTO GALKA, сейчас: {row.get('status')}"
                )
            if self._active_campaign_locked(normalized):
                raise LiveEngineError("Сначала должна полностью завершиться текущая GALKA")
        return self._activate_queue(normalized, manual=True)

    def _pause_queue_locked(self, row: dict[str, Any], reason: str) -> None:
        if row.get("status") not in AUTO_WATCH_STATUSES and row.get("status") != "activating":
            return
        if row.get("status") == "paused" and row.get("pausedReason") == reason:
            return
        row["status"] = "paused"
        row["pausedAt"] = now_iso()
        row["pausedReason"] = reason
        row["lastCheckedAt"] = now_iso()
        self._event_locked(
            "live",
            f"{row['coin']}: AUTO GALKA {row['galkaPrice']:g} поставлена на паузу — {reason}",
            autoQueue=True,
            queueId=row.get("id"),
            sourceCampaignId=row.get("sourceCampaignId"),
        )
        self._save_locked()

    def _invalidate_queue_locked(
        self,
        row: dict[str, Any],
        reason: str,
        *,
        observed_price: float | None = None,
        observed_at_ms: int | None = None,
    ) -> None:
        if row.get("status") == "invalidated":
            return
        row["status"] = "invalidated"
        row["invalidatedAt"] = now_iso()
        row["invalidatedReason"] = reason
        row["lastCheckedAt"] = now_iso()
        if observed_price is not None:
            row["invalidatedObservedPrice"] = observed_price
        if observed_at_ms is not None:
            row["invalidatedObservedAtMs"] = int(observed_at_ms)
        self._event_locked(
            "live",
            f"{row['coin']}: AUTO GALKA {row['galkaPrice']:g} INVALIDATED — уровень уже был перебит",
            autoQueue=True,
            queueId=row.get("id"),
            reason=reason,
            observedPrice=observed_price,
            observedAtMs=observed_at_ms,
        )
        self._save_locked()

    def _cached_mid(self, coin: str) -> float | None:
        """Read only the existing mids cache so AUTO watching never blocks trading I/O."""
        try:
            getter = getattr(self.gateway, "_cache_get", None)
            rows = getter("mids", 3.0) if callable(getter) else None
            if isinstance(rows, dict):
                value = float(rows.get(coin) or 0)
                return value if value > 0 else None
        except Exception:
            pass
        return None

    @staticmethod
    def _history_interval(age_ms: int) -> tuple[str, int]:
        if age_ms <= 24 * 60 * 60 * 1000:
            return "1m", 60_000
        if age_ms <= 60 * 24 * 60 * 60 * 1000:
            return "1h", 3_600_000
        return "1d", 86_400_000

    def _history_touch_check(self, row: dict[str, Any]) -> dict[str, Any]:
        queued_ms = int(row.get("queuedMs") or 0)
        level = float(row.get("galkaPrice") or 0)
        coin = str(row.get("coin") or "")
        if queued_ms <= 0 or level <= 0:
            raise LiveEngineError("Повреждены данные AUTO GALKA")

        current_mid = float(self.gateway.mids().get(coin) or 0)
        if current_mid <= level:
            return {
                "touched": True,
                "basis": "current_mid",
                "price": current_mid,
                "timeMs": now_ms(),
            }

        age_ms = max(0, now_ms() - queued_ms)
        interval, interval_ms = self._history_interval(age_ms)
        needed = int(math.ceil(age_ms / interval_ms)) + 12
        if needed > 1500:
            raise LiveEngineError(
                "AUTO GALKA слишком долго была без проверяемой истории; автоматическая активация запрещена"
            )
        candles = self.gateway.candles(coin, interval, max(50, needed))
        if not candles:
            raise LiveEngineError("Hyperliquid не вернул историю для проверки AUTO GALKA")

        relevant = [
            candle
            for candle in candles
            if int(candle.get("closeTime") or 0) >= queued_ms
        ]
        if not relevant:
            raise LiveEngineError("История Hyperliquid не покрывает время постановки AUTO GALKA")
        earliest_open = min(int(candle.get("openTime") or 0) for candle in relevant)
        if earliest_open > queued_ms + interval_ms:
            raise LiveEngineError("В истории AUTO GALKA обнаружен непроверенный временной разрыв")

        for candle in relevant:
            low = float(candle.get("low") or 0)
            if low > 0 and low <= level:
                return {
                    "touched": True,
                    "basis": f"{interval}_candle_low",
                    "price": low,
                    "timeMs": int(candle.get("openTime") or 0),
                    "openTime": int(candle.get("openTime") or 0),
                    "closeTime": int(candle.get("closeTime") or 0),
                }
        return {
            "touched": False,
            "basis": f"{interval}_candles+current_mid",
            "checkedAt": now_iso(),
            "currentMid": current_mid,
            "candlesChecked": len(relevant),
        }

    def _revalidate_persisted_queues(self) -> None:
        with self.lock:
            rows = [
                deepcopy(row)
                for row in self.state.setdefault("queuedGalkas", {}).values()
                if isinstance(row, dict) and row.get("status") in AUTO_WATCH_STATUSES | {"activating"}
            ]

        for snapshot in rows:
            coin = str(snapshot.get("coin") or "")
            if snapshot.get("status") == "activating":
                with self.lock:
                    row = self._queue_locked(coin)
                    campaign = self.state.get("campaigns", {}).get(coin)
                    if not row or row.get("id") != snapshot.get("id"):
                        continue
                    if (
                        campaign
                        and str(campaign.get("id") or "") != str(row.get("sourceCampaignId") or "")
                        and campaign.get("status") in ACTIVE_STATUSES
                    ):
                        row["status"] = "activated"
                        row["activatedAt"] = now_iso()
                        row["activatedCampaignId"] = campaign.get("id")
                        row["lastError"] = None
                        self._save_locked()
                    else:
                        self._pause_queue_locked(
                            row,
                            "приложение перезапустилось во время AUTO-активации; нужна ручная проверка",
                        )
                continue

            try:
                check = self._history_touch_check(snapshot)
            except Exception as exc:
                with self.lock:
                    row = self._queue_locked(coin)
                    if row and row.get("id") == snapshot.get("id"):
                        row["lastError"] = str(exc)
                        self._pause_queue_locked(
                            row,
                            "не удалось доказать, что уровень не был перебит после перезапуска",
                        )
                continue

            with self.lock:
                row = self._queue_locked(coin)
                if not row or row.get("id") != snapshot.get("id"):
                    continue
                row["historyValidation"] = check
                row["lastCheckedAt"] = now_iso()
                if check.get("touched"):
                    self._invalidate_queue_locked(
                        row,
                        str(check.get("basis") or "history_touch"),
                        observed_price=float(check.get("price") or 0),
                        observed_at_ms=int(check.get("timeMs") or 0),
                    )
                else:
                    self._save_locked()

    def _activate_queue(self, coin: str, *, manual: bool) -> dict[str, Any]:
        with self.lock:
            row = self._queue_locked(coin)
            if not row:
                raise LiveEngineError(f"Для {coin} нет AUTO GALKA")
            expected_status = "paused" if manual else "queued"
            if row.get("status") != expected_status:
                raise LiveEngineError(
                    f"AUTO GALKA {coin} уже сменила состояние: {row.get('status')}"
                )
            if self._active_campaign_locked(coin):
                raise LiveEngineError("Текущая GALKA ещё активна")
            system = self.state.get("system", {})
            if system.get("safeMode"):
                if not manual:
                    self._pause_queue_locked(row, "SAFE MODE запрещает автоматическую активацию")
                raise LiveEngineError(
                    f"SAFE MODE: {system.get('safeModeReason') or 'активация AUTO запрещена'}"
                )
            snapshot = deepcopy(row)

        try:
            check = self._history_touch_check(snapshot)
        except Exception as exc:
            with self.lock:
                row = self._queue_locked(coin)
                if row and row.get("id") == snapshot.get("id"):
                    row["lastError"] = str(exc)
                    self._pause_queue_locked(row, "не удалось проверить историю перед активацией")
            raise LiveEngineError(f"AUTO GALKA не активирована: {exc}") from exc

        if check.get("touched"):
            with self.lock:
                row = self._queue_locked(coin)
                if row and row.get("id") == snapshot.get("id"):
                    row["historyValidation"] = check
                    self._invalidate_queue_locked(
                        row,
                        str(check.get("basis") or "history_touch"),
                        observed_price=float(check.get("price") or 0),
                        observed_at_ms=int(check.get("timeMs") or 0),
                    )
            raise LiveEngineError("AUTO GALKA не активирована: её уровень уже был перебит")

        with self.lock:
            row = self._queue_locked(coin)
            if not row or row.get("id") != snapshot.get("id"):
                raise LiveEngineError("AUTO GALKA была заменена во время проверки")
            row["status"] = "activating"
            row["historyValidation"] = check
            row["lastCheckedAt"] = now_iso()
            row["lastError"] = None
            self._event_locked(
                "live",
                f"{coin}: AUTO GALKA {row['galkaPrice']:g} активируется",
                autoQueue=True,
                queueId=row.get("id"),
                manual=manual,
            )
            self._save_locked()
            price = float(row["galkaPrice"])
            queue_id = str(row["id"])

        try:
            # The user's QUEUE_REAL_GALKA confirmation explicitly authorizes this
            # later activation. The normal create_campaign path still performs all
            # current equity, SAFE MODE, clean-account and exchange validations.
            campaign = self.create_campaign(coin, price, "PLACE_REAL_ORDERS")
        except Exception as exc:
            with self.lock:
                row = self._queue_locked(coin)
                if row and row.get("id") == queue_id:
                    row["lastError"] = str(exc)
                    try:
                        mid = float(self.gateway.mids().get(coin) or 0)
                    except Exception:
                        mid = 0.0
                    if mid > 0 and mid <= price:
                        self._invalidate_queue_locked(
                            row,
                            "price_reached_during_activation",
                            observed_price=mid,
                            observed_at_ms=now_ms(),
                        )
                    else:
                        self._pause_queue_locked(row, f"ошибка AUTO-активации: {exc}")
            if isinstance(exc, LiveEngineError):
                raise
            raise LiveEngineError(str(exc)) from exc

        with self.lock:
            row = self._queue_locked(coin)
            if row and row.get("id") == queue_id:
                row["status"] = "activated"
                row["activatedAt"] = now_iso()
                row["activatedCampaignId"] = campaign.get("id")
                row["lastError"] = None
                self._event_locked(
                    "live",
                    f"{coin}: AUTO GALKA {price:g} активирована после предыдущей кампании",
                    autoQueue=True,
                    queueId=queue_id,
                    campaignId=campaign.get("id"),
                    manual=manual,
                )
                self._save_locked()
        return {"queue": self.queued_galka_status(coin), "campaign": campaign}

    def _auto_queue_loop(self) -> None:
        # Re-check any persisted queue against venue history before it can ever
        # auto-activate after an app restart.
        try:
            self._revalidate_persisted_queues()
        except Exception:
            pass

        while not self.stop_event.wait(0.75):
            with self.lock:
                snapshots = [
                    deepcopy(row)
                    for row in self.state.setdefault("queuedGalkas", {}).values()
                    if isinstance(row, dict) and row.get("status") in AUTO_WATCH_STATUSES
                ]

            for snapshot in snapshots:
                coin = str(snapshot.get("coin") or "")
                level = float(snapshot.get("galkaPrice") or 0)
                cached_mid = self._cached_mid(coin)
                if cached_mid is not None and cached_mid <= level:
                    with self.lock:
                        row = self._queue_locked(coin)
                        if row and row.get("id") == snapshot.get("id"):
                            self._invalidate_queue_locked(
                                row,
                                "cached_mid_touch",
                                observed_price=cached_mid,
                                observed_at_ms=now_ms(),
                            )
                    continue

                with self.lock:
                    row = self._queue_locked(coin)
                    if not row or row.get("id") != snapshot.get("id"):
                        continue
                    if row.get("status") != "queued":
                        continue
                    source = self.state.get("campaigns", {}).get(coin)
                    source_id = str(row.get("sourceCampaignId") or "")
                    if not source or str(source.get("id") or "") != source_id:
                        self._pause_queue_locked(
                            row,
                            "исходная кампания больше не является текущей; нужна ручная активация",
                        )
                        continue
                    source_status = str(source.get("status") or "")
                    if source_status in {"recovery", "emergency", "canceling"}:
                        self._pause_queue_locked(
                            row,
                            f"исходная кампания перешла в {source_status}; AUTO запрещено",
                        )
                        continue
                    if source_status in {"canceled", "recovery_closed", "emergency_closed"}:
                        self._pause_queue_locked(
                            row,
                            f"исходная кампания завершилась как {source_status}; нужна ручная активация",
                        )
                        continue
                    should_activate = source_status == "completed"

                if should_activate:
                    try:
                        self._activate_queue(coin, manual=False)
                    except Exception:
                        # _activate_queue persists invalidated/paused state itself.
                        pass

    def cancel_waiting_campaign(self, coin: str) -> dict[str, Any]:
        normalized = self._coin(coin)
        with self.lock:
            source = self._active_campaign_locked(normalized)
            source_id = str(source.get("id") or "") if source else ""
        try:
            result = super().cancel_waiting_campaign(normalized)
        except Exception:
            with self.lock:
                row = self._queue_locked(normalized)
                current = self._campaign_locked(normalized)
                if (
                    row
                    and row.get("status") == "queued"
                    and str(row.get("sourceCampaignId") or "") == source_id
                    and current
                    and current.get("status") == "recovery"
                ):
                    self._pause_queue_locked(row, "отмена исходной GALKA перешла в recovery")
            raise

        with self.lock:
            row = self._queue_locked(normalized)
            if (
                row
                and row.get("status") == "queued"
                and str(row.get("sourceCampaignId") or "") == source_id
            ):
                self._pause_queue_locked(
                    row,
                    "исходная GALKA отменена пользователем; AUTO ждёт ручного решения",
                )
        return result

    def close_near_market(self, coin: str, confirmation: str) -> dict[str, Any]:
        normalized = self._coin(coin)
        with self.lock:
            source = self._active_campaign_locked(normalized)
            row = self._queue_locked(normalized)
            if (
                source
                and row
                and row.get("status") == "queued"
                and str(row.get("sourceCampaignId") or "") == str(source.get("id") or "")
            ):
                self._pause_queue_locked(
                    row,
                    "по исходной GALKA запрошен ручной выход; AUTO отключено",
                )
        return super().close_near_market(normalized, confirmation)

    def emergency_close(self, coin: str, confirmation: str) -> dict[str, Any]:
        normalized = self._coin(coin)
        with self.lock:
            source = self._active_campaign_locked(normalized)
            row = self._queue_locked(normalized)
            if (
                source
                and row
                and row.get("status") == "queued"
                and str(row.get("sourceCampaignId") or "") == str(source.get("id") or "")
            ):
                self._pause_queue_locked(
                    row,
                    "по исходной GALKA запрошено аварийное закрытие; AUTO отключено",
                )
        return super().emergency_close(normalized, confirmation)

    def status(self) -> dict[str, Any]:
        result = super().status()
        with self.lock:
            result["queuedGalkas"] = deepcopy(self.state.setdefault("queuedGalkas", {}))
        return result

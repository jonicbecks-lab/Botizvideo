from __future__ import annotations

import os
import subprocess
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

from .engine import ACTIVE_STATUSES
from .hyperliquid_gateway import SUPPORTED_COINS
from .live_ladder import MANUAL_DEPTHS, MANUAL_WEIGHTS


STATUS_WORKING = "working"
STATUS_PROBLEM = "problem"
STATUS_PARTIAL = "partial"
STATUS_DISCONNECTED = "disconnected"
STATUS_MANUAL = "manual"


class ProjectControlCenter:
    """Read-only project map and diagnostics for the production GALKA LIVE runtime.

    The control center intentionally owns no exchange mutation method. Its live check
    performs only reads and never clears SAFE MODE, cancels orders, places orders or
    changes campaign state.
    """

    def __init__(self, engine: Any):
        self.engine = engine
        self.repo_root = Path(__file__).resolve().parents[1]

    @staticmethod
    def _now_ms() -> int:
        return int(time.time() * 1000)

    def _git_sha(self) -> str | None:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=self.repo_root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=3,
                check=False,
            )
            value = (result.stdout or "").strip()
            return value if len(value) == 40 else None
        except Exception:
            return None

    def _read_text(self, relative: str) -> str:
        try:
            path = self.repo_root / relative
            if not path.is_file() or path.is_symlink():
                return ""
            return path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return ""

    def _runtime_snapshot(self) -> dict[str, Any]:
        with self.engine.lock:
            state = deepcopy(self.engine.state)
        system = state.get("system") or {}
        campaigns = [
            deepcopy(row)
            for row in (state.get("campaigns") or {}).values()
            if isinstance(row, dict) and row.get("status") in ACTIVE_STATUSES
        ]
        queues = [
            deepcopy(row)
            for row in (state.get("queuedGalkas") or {}).values()
            if isinstance(row, dict)
        ]
        monitor = getattr(self.engine, "monitor_thread", None)
        auto_thread = getattr(self.engine, "_auto_queue_thread", None)
        return {
            "system": system,
            "campaigns": campaigns,
            "queues": queues,
            "monitorStarted": bool(monitor and monitor.ident is not None),
            "monitorAlive": bool(monitor and monitor.is_alive()),
            "autoQueueAlive": bool(auto_thread and auto_thread.is_alive()),
        }

    def _market_snapshot(self) -> dict[str, Any]:
        account: dict[str, Any] | None = None
        mids: dict[str, float] | None = None
        account_error = None
        mids_error = None
        try:
            account = self.engine.gateway.account_state()
        except Exception as exc:
            account_error = f"{type(exc).__name__}: {exc}"
        try:
            mids = self.engine.gateway.mids()
        except Exception as exc:
            mids_error = f"{type(exc).__name__}: {exc}"
        return {
            "account": account,
            "mids": mids,
            "accountError": account_error,
            "midsError": mids_error,
        }

    def _research_snapshot(self) -> dict[str, Any]:
        recorder: dict[str, Any]
        try:
            recorder = self.engine.research_recorder.status()
        except Exception as exc:
            recorder = {"enabled": False, "error": str(exc)}

        journal = getattr(self.engine, "research_journal", None)
        journal_root = getattr(journal, "root", None)
        sync_thread = getattr(journal, "_sync_thread", None)
        manifest = getattr(journal, "manifest_path", None)
        latest_local_ms = None
        try:
            if manifest and Path(manifest).is_file():
                latest_local_ms = int(Path(manifest).stat().st_mtime * 1000)
        except OSError:
            pass

        cluster: dict[str, Any]
        service = getattr(self.engine, "cluster_volume", None)
        if service is None:
            cluster = {"enabled": False, "connected": False}
        else:
            try:
                from .cluster_volume import ClusterVolumeService

                cluster = ClusterVolumeService.status(service)
                cluster["archivePersistent"] = True
            except Exception as exc:
                cluster = {"enabled": False, "connected": False, "error": str(exc)}

        return {
            "recorder": recorder,
            "journalRoot": str(journal_root) if journal_root else None,
            "journalWritable": bool(journal_root and os.access(journal_root, os.W_OK)),
            "syncThreadAlive": bool(sync_thread and sync_thread.is_alive()),
            "latestLocalManifestMs": latest_local_ms,
            "cluster": cluster,
        }

    def _updater_snapshot(self) -> dict[str, Any]:
        try:
            from .update_manager_v2 import manager_for

            return manager_for(self.engine).status()
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}

    def _logic_audit(self) -> list[dict[str, Any]]:
        readme = self._read_text("README.md")
        constitution = self._read_text("GALKA_CONSTITUTION.md")
        hardened = self._read_text("HARDENED_LIVE_README_RU.md")
        live_status = self._read_text("LIVE_IMPLEMENTATION_STATUS.md")
        safe_compat = self._read_text("live/hyperliquid_safe_compat.py")
        config = self._read_text("live/config.py")
        runtime_coins = sorted(str(coin) for coin in SUPPORTED_COINS)
        warnings: list[dict[str, Any]] = []

        paper_contract = (
            "paper-only" in readme.lower()
            and "binance" in readme.lower()
            and "paper-only" in constitution.lower()
        )
        if paper_contract:
            warnings.append(
                {
                    "id": "paper-vs-live",
                    "level": "important",
                    "title": "Корневая концепция и фактически открытый LIVE-контур расходятся",
                    "text": (
                        "README и Galka Pro Constitution описывают PAPER-only терминал на Binance без реальных ордеров. "
                        "Текущий рабочий интерфейс terminal/live.html — отдельный real-money Hyperliquid-контур. "
                        "Это уже фактически два продукта в одном репозитории, а не одна единая концепция."
                    ),
                }
            )

        if "BTC/ETH/SOL" in hardened or "BTC/ETH/SOL" in live_status:
            if "BNB" in runtime_coins and "SOL" not in runtime_coins:
                warnings.append(
                    {
                        "id": "coin-universe",
                        "level": "warning",
                        "title": "Документация по монетам устарела",
                        "text": (
                            "Hardened LIVE-документация всё ещё говорит BTC/ETH/SOL, а production runtime сейчас работает с "
                            + "/".join(runtime_coins)
                            + "."
                        ),
                    }
                )

        if "L1 rearm" in hardened and "L1 rearm отключён" in safe_compat:
            warnings.append(
                {
                    "id": "l1-rearm",
                    "level": "important",
                    "title": "Поведение L1 изменилось относительно старой LIVE-документации",
                    "text": (
                        "Hardened README ещё требует проверять L1 rearm. В текущем production-коде L1 после закрытия на GALKA "
                        "завершает всю кампанию; автоматический rearm L1 отключён."
                    ),
                }
            )

        if "$1000" in hardened and "total_notional > 5000" in config:
            warnings.append(
                {
                    "id": "notional-limit",
                    "level": "warning",
                    "title": "Лимит номинала в документации не совпадает с конфигурацией",
                    "text": "Hardened README описывает максимум $1000, а текущая конфигурация разрешает до $5000.",
                }
            )

        if "returnToGalka" in self._read_text("live/research_recorder.py"):
            warnings.append(
                {
                    "id": "research-return-semantics",
                    "level": "partial",
                    "title": "Research: возврат к GALKA пока не привязан строго к первому fill",
                    "text": (
                        "Recorder отмечает returnToGalka после факта ухода цены ниже GALKA. Это полезно для рыночной траектории, "
                        "но для Детектива событие нельзя автоматически трактовать как «возврат после первого исполнения» без дополнительной проверки fill-времени."
                    ),
                }
            )
        return warnings

    def _flow(self, runtime: dict[str, Any], research: dict[str, Any]) -> list[dict[str, Any]]:
        safe = bool((runtime.get("system") or {}).get("safeMode"))
        recorder_enabled = bool((research.get("recorder") or {}).get("enabled"))
        return [
            {
                "id": "market",
                "title": "Рынок и счёт Hyperliquid",
                "summary": "Получаем цену, свечи, позиции и ордера биржи.",
                "receives": "Публичные котировки и приватное состояние выбранного Hyperliquid-аккаунта.",
                "does": "Разделяет публичные данные графика и критические торговые чтения, чтобы график не тормозил исполнение.",
                "outputs": "Свежая цена, свечи, баланс, позиции и список ордеров для остальных этапов.",
                "status": STATUS_PROBLEM if safe else STATUS_WORKING,
            },
            {
                "id": "manual-setup",
                "title": "Ручная GALKA и её форма",
                "summary": "Пользователь выбирает уровень GALKA, якорь и левую/правую часть структуры.",
                "receives": "Перекрестие графика и ручную разметку пользователя.",
                "does": "Фиксирует точную цену GALKA и research-геометрию V3; геометрия не меняет торговую формулу.",
                "outputs": "Цена GALKA для торговли + отдельный researchSetup для последующего анализа.",
                "status": STATUS_WORKING,
            },
            {
                "id": "ladder",
                "title": "Расчёт 8 уровней",
                "summary": "Строим восемь лимитных входов ниже GALKA и распределяем капитал.",
                "receives": "Цена GALKA, текущий капитал, 10x isolated и лимит доли маржи.",
                "does": "Применяет фиксированные глубины/веса, минимум Hyperliquid и динамический размер от капитала.",
                "outputs": "L1–L8 с ценой, размером и notional; для каждого уровня — цель на GALKA.",
                "status": STATUS_WORKING,
            },
            {
                "id": "exchange",
                "title": "Отправка ордеров",
                "summary": "После финального подтверждения реальные ордера уходят на Hyperliquid.",
                "receives": "Проверенную сетку L1–L8 и явное подтверждение пользователя.",
                "does": "Проверяет SAFE MODE, текущие позиции/ордера, ставит isolated leverage и биржевые входы + reduce-only TP.",
                "outputs": "Биржевые OID/CLOID и локальную кампанию, привязанную к реальным ордерам.",
                "status": STATUS_PROBLEM if safe else STATUS_WORKING,
            },
            {
                "id": "monitor",
                "title": "Сверка и завершение",
                "summary": "Следим за fill, позицией и возвратом к GALKA до полного закрытия.",
                "receives": "Fill, реальные позиции и открытые ордера Hyperliquid.",
                "does": "Сверяет ownership, не доверяет одному локальному состоянию; при расхождении включает recovery/SAFE MODE. После L1 закрытия rearm не выполняется — кампания завершается.",
                "outputs": "Completed/canceled/recovery статус, PnL и чистое биржевое состояние.",
                "status": STATUS_WORKING if runtime.get("monitorAlive") and not safe else STATUS_PROBLEM,
            },
            {
                "id": "research",
                "title": "Параллельный research-контур",
                "summary": "Записываем то, что поможет Детективу анализировать GALKA, не вмешиваясь в торговлю.",
                "receives": "researchSetup, fills, события, свечи, trades/L2 и кластерные данные.",
                "does": "Пишет локальный журнал, optional high-frequency recorder и компактно синхронизирует research-данные в GitHub.",
                "outputs": "Campaign datasets, события, footprint/кластеры и данные для внешнего анализа.",
                "status": STATUS_WORKING if recorder_enabled else STATUS_PARTIAL,
                "parallel": True,
            },
        ]

    def _system_cards(
        self,
        runtime: dict[str, Any],
        market: dict[str, Any],
        research: dict[str, Any],
        updater: dict[str, Any],
    ) -> list[dict[str, Any]]:
        system = runtime.get("system") or {}
        safe = bool(system.get("safeMode"))
        recorder = research.get("recorder") or {}
        cluster = research.get("cluster") or {}
        account = market.get("account")
        mids = market.get("mids")
        return [
            {
                "id": "live-engine",
                "title": "LIVE-движок и монитор",
                "status": STATUS_PROBLEM if safe or not runtime.get("monitorAlive") else STATUS_WORKING,
                "detail": (
                    str(system.get("safeModeReason") or "LIVE-монитор работает и периодически сверяет биржу.")
                    if safe or runtime.get("monitorAlive")
                    else "Фоновый LIVE-монитор не работает."
                ),
            },
            {
                "id": "hyperliquid-account",
                "title": "Счёт Hyperliquid",
                "status": STATUS_WORKING if isinstance(account, dict) else STATUS_PROBLEM,
                "detail": (
                    f"Баланс ${float(account.get('accountValue') or 0):.2f}; источник: {account.get('balanceSource') or account.get('accountMode') or 'perp account'}."
                    if isinstance(account, dict)
                    else str(market.get("accountError") or "Состояние счёта недоступно.")
                ),
            },
            {
                "id": "market-data",
                "title": "Цена и свечи",
                "status": STATUS_WORKING if isinstance(mids, dict) and mids else STATUS_PROBLEM,
                "detail": "Публичные данные идут по отдельному read-only пути." if mids else str(market.get("midsError") or "Котировки недоступны."),
            },
            {
                "id": "auto-queue",
                "title": "AUTO следующей GALKA",
                "status": STATUS_WORKING if runtime.get("autoQueueAlive") else STATUS_PARTIAL,
                "detail": f"Очередей сейчас: {len(runtime.get('queues') or [])}. AUTO активируется только после нормального завершения исходной кампании.",
            },
            {
                "id": "research-journal",
                "title": "Research-журнал",
                "status": STATUS_WORKING if research.get("journalWritable") and research.get("syncThreadAlive") else STATUS_PARTIAL,
                "detail": "Локальная запись отделена от торговли; GitHub-sync запускается best-effort примерно раз в 5 минут.",
            },
            {
                "id": "research-recorder",
                "title": "Высокочастотный Recorder",
                "status": STATUS_WORKING if recorder.get("enabled") else STATUS_DISCONNECTED,
                "detail": (
                    f"Включён; активных research-сессий: {len(recorder.get('activeSessions') or [])}."
                    if recorder.get("enabled")
                    else "Отключён конфигурацией. Торговля от него не зависит."
                ),
            },
            {
                "id": "clusters",
                "title": "Кластерный поток",
                "status": STATUS_WORKING if cluster.get("connected") else STATUS_PARTIAL,
                "detail": (
                    "Публичный trades WebSocket подключён; архив кластеров сохраняется локально."
                    if cluster.get("connected")
                    else str(cluster.get("lastError") or cluster.get("error") or "Поток сейчас не подтверждён как подключён; торговля от него не зависит.")
                ),
            },
            {
                "id": "updater",
                "title": "Безопасное обновление",
                "status": STATUS_PROBLEM if updater.get("error") else STATUS_PARTIAL if updater.get("worktreeClean") is False else STATUS_WORKING,
                "detail": (
                    str(updater.get("error"))
                    if updater.get("error")
                    else "Автообновление заблокировано локальными изменениями Git."
                    if updater.get("worktreeClean") is False
                    else "Fast-forward updater с backup, тестами, health-check и rollback готов."
                ),
            },
        ]

    def _connections(
        self,
        market: dict[str, Any],
        research: dict[str, Any],
        updater: dict[str, Any],
    ) -> list[dict[str, Any]]:
        cluster = research.get("cluster") or {}
        recorder = research.get("recorder") or {}
        return [
            {
                "title": "Hyperliquid · приватный REST",
                "purpose": "Баланс, позиции, ордера, fills и торговые команды.",
                "status": STATUS_WORKING if isinstance(market.get("account"), dict) else STATUS_PROBLEM,
            },
            {
                "title": "Hyperliquid · публичные котировки/свечи",
                "purpose": "График, mid-цены, history и проверки AUTO.",
                "status": STATUS_WORKING if market.get("mids") else STATUS_PROBLEM,
            },
            {
                "title": "Hyperliquid · trades WebSocket",
                "purpose": "Кластерный объём; отдельный research-поток, не торговый путь.",
                "status": STATUS_WORKING if cluster.get("connected") else STATUS_PARTIAL,
            },
            {
                "title": "Hyperliquid · Research WebSocket",
                "purpose": "BBO, trades и L2 для campaign recorder.",
                "status": STATUS_PARTIAL if recorder.get("enabled") else STATUS_DISCONNECTED,
                "note": "Текущий Recorder не публикует отдельный постоянный health-флаг WebSocket; поэтому зелёный статус не симулируется.",
            },
            {
                "title": "Локальное хранилище Termux",
                "purpose": "State, research journal, raw recorder, кластеры, updater backups.",
                "status": STATUS_WORKING if research.get("journalWritable") else STATUS_PROBLEM,
            },
            {
                "title": "GitHub · рабочая ветка GALKA",
                "purpose": "Проверка и установка новых версий через встроенный updater.",
                "status": STATUS_PARTIAL if updater.get("error") else STATUS_WORKING,
                "note": "Фактический доступ к GitHub проверяется кнопкой обновления; обычный статус не делает лишний сетевой fetch.",
            },
            {
                "title": "GitHub · data/galka-live-journal",
                "purpose": "Компактная research-копия кампаний, событий, footprint и кластеров.",
                "status": STATUS_PARTIAL,
                "note": "Sync best-effort: runtime подтверждает локальный scheduler, но не выдаёт ложный зелёный статус удалённого push.",
            },
        ]

    def overview(self) -> dict[str, Any]:
        runtime = self._runtime_snapshot()
        market = self._market_snapshot()
        research = self._research_snapshot()
        updater = self._updater_snapshot()
        audit = self._logic_audit()
        config = self.engine.config
        safe = bool((runtime.get("system") or {}).get("safeMode"))
        sha = self._git_sha()

        return {
            "generatedAtMs": self._now_ms(),
            "version": {"sha": sha, "short": sha[:7] if sha else None},
            "project": {
                "title": "GALKA LIVE · Hyperliquid",
                "concept": (
                    "Реальный long-only терминал: пользователь вручную задаёт GALKA, приложение рассчитывает 8 входов ниже неё, "
                    "ставит биржевые цели обратно на GALKA, сверяет фактическое состояние Hyperliquid и отдельно собирает research-данные."
                ),
                "network": config.network_name,
                "liveEnabled": bool(config.live_enabled),
                "account": config.masked_address,
                "coins": sorted(SUPPORTED_COINS),
                "leverage": int(config.leverage),
                "isolated": bool(config.isolated),
                "maxMarginFraction": float(config.max_margin_fraction),
                "activeCampaigns": len(runtime.get("campaigns") or []),
                "safeMode": safe,
                "safeModeReason": (runtime.get("system") or {}).get("safeModeReason"),
                "automaticStopLoss": False,
            },
            "flow": self._flow(runtime, research),
            "system": self._system_cards(runtime, market, research, updater),
            "connections": self._connections(market, research, updater),
            "scope": {
                "working": [
                    "Реальные long-only GALKA на Hyperliquid с 8 уровнями и reduce-only целями на GALKA.",
                    "10x isolated, динамический размер от капитала и общий лимит доли маржи.",
                    "Fail-closed сверка позиций/ордеров, SAFE MODE, recovery, безопасная отмена и аварийное закрытие.",
                    "AUTO следующей GALKA после нормального завершения кампании.",
                    "Ручная research-разметка V3: якорь, левая и правая граница.",
                    "Кластеры объёма и встроенный updater с rollback.",
                ],
                "partial": [
                    "High-frequency Research Recorder включается конфигурацией и может быть отключён.",
                    "GitHub research-sync best-effort; raw trades/L2 специально остаются локально.",
                    "Детектив пока не является частью торгового контура: GALKA только готовит данные для внешнего анализа.",
                    "Событие research returnToGalka нельзя без проверки fill-времени считать строго «возвратом после первого fill».",
                ],
                "planned": [
                    "Подключить Детектив к чистой V3-выборке и проверять continuation после обычного выхода.",
                    "После статистического подтверждения отдельно обсуждать любые изменения стратегии; автоматически они не применяются.",
                    "Свести PAPER и LIVE документацию к явным отдельным продуктовым контрактам.",
                ],
                "known": [item["title"] for item in audit],
            },
            "logicAudit": {
                "matchesSingleDocumentedConcept": not audit,
                "warning": (
                    "Сейчас приложение и документация не описываются одной единой концепцией. Ниже показаны конкретные расхождения."
                    if audit
                    else None
                ),
                "items": audit,
            },
            "strategy": {
                "depthsPct": list(MANUAL_DEPTHS),
                "weightsPct": [round(value * 100.0, 4) for value in MANUAL_WEIGHTS],
                "campaignCompletion": "После закрытия позиции на GALKA кампания завершается; L1 rearm отключён.",
                "researchAffectsTrading": False,
            },
            "lastCheck": getattr(self.engine, "_project_control_center_last_check", None),
        }

    def _check_row(self, key: str, title: str, status: str, detail: str, ms: int | None = None) -> dict[str, Any]:
        return {"id": key, "title": title, "status": status, "detail": detail, "ms": ms}

    def _timed(self, action: Any) -> tuple[Any, float]:
        started = time.monotonic()
        result = action()
        return result, round((time.monotonic() - started) * 1000, 1)

    def _consistency_check(self, account: dict[str, Any], orders: list[dict[str, Any]]) -> tuple[str, str]:
        with self.engine.lock:
            campaigns = {
                str(coin): deepcopy(campaign)
                for coin, campaign in (self.engine.state.get("campaigns") or {}).items()
                if isinstance(campaign, dict) and campaign.get("status") in ACTIVE_STATUSES
            }
            safe_mode = bool((self.engine.state.get("system") or {}).get("safeMode"))
            safe_reason = (self.engine.state.get("system") or {}).get("safeModeReason")

        problems: list[str] = []
        for coin in sorted(SUPPORTED_COINS):
            campaign = campaigns.get(coin)
            position = (account.get("positions") or {}).get(coin) or {}
            try:
                actual = float(position.get("size") or 0)
            except (TypeError, ValueError):
                actual = 0.0
            coin_orders = [row for row in orders if str(row.get("coin") or "") == coin]
            tolerance = self.engine._size_tolerance(coin)
            if campaign is None:
                if abs(actual) > tolerance:
                    problems.append(f"{coin}: позиция без активной кампании")
                if coin_orders:
                    problems.append(f"{coin}: {len(coin_orders)} ордер(ов) без активной кампании")
                continue
            if campaign.get("status") == "recovery":
                problems.append(f"{coin}: кампания в recovery")
            if actual < -tolerance:
                problems.append(f"{coin}: обнаружена short-позиция")
            foreign = []
            try:
                foreign = self.engine._foreign_open_orders(campaign, coin_orders)
            except Exception:
                pass
            if foreign:
                problems.append(f"{coin}: {len(foreign)} посторонних ордер(ов)")
            try:
                managed = float(campaign.get("managedNetSize") or 0)
                if abs(actual - managed) > tolerance and campaign.get("status") not in {"placing", "canceling", "closing", "emergency"}:
                    problems.append(f"{coin}: позиция биржи и managed size расходятся")
            except (TypeError, ValueError):
                problems.append(f"{coin}: некорректный managed size")

        if safe_mode:
            problems.append("SAFE MODE: " + str(safe_reason or "причина не указана"))
        if problems:
            return STATUS_PROBLEM, "; ".join(problems[:8])
        return STATUS_WORKING, "Активные кампании, позиции и ордера не показывают очевидных orphan/foreign расхождений."

    def check_now(self, coin: str = "BTC") -> dict[str, Any]:
        selected = str(coin or "BTC").upper()
        if selected not in SUPPORTED_COINS:
            selected = sorted(SUPPORTED_COINS)[0]
        started = time.monotonic()
        rows: list[dict[str, Any]] = []

        monitor = getattr(self.engine, "monitor_thread", None)
        monitor_ok = bool(monitor and monitor.is_alive())
        rows.append(
            self._check_row(
                "monitor",
                "LIVE-монитор",
                STATUS_WORKING if monitor_ok else STATUS_PROBLEM,
                "Фоновая сверка работает." if monitor_ok else "Фоновая сверка остановлена.",
            )
        )

        try:
            mids, ms = self._timed(self.engine.gateway.mids)
            price = float((mids or {}).get(selected) or 0)
            if price <= 0:
                raise RuntimeError(f"Нет mid-цены {selected}")
            rows.append(self._check_row("mids", "Цена Hyperliquid", STATUS_WORKING, f"{selected}: {price:g}", ms))
        except Exception as exc:
            rows.append(self._check_row("mids", "Цена Hyperliquid", STATUS_PROBLEM, str(exc)))

        try:
            candles, ms = self._timed(lambda: self.engine.gateway.candles(selected, "5m", 50))
            rows.append(
                self._check_row(
                    "candles",
                    "Свечи Hyperliquid",
                    STATUS_WORKING if candles else STATUS_PROBLEM,
                    f"Получено {len(candles)} последних 5m свечей." if candles else "Свечи не получены.",
                    ms,
                )
            )
        except Exception as exc:
            rows.append(self._check_row("candles", "Свечи Hyperliquid", STATUS_PROBLEM, str(exc)))

        action_lock = getattr(self.engine, "action_lock", None)
        acquired = bool(action_lock and action_lock.acquire(timeout=0.35))
        account = None
        orders = None
        pending = getattr(self.engine, "_manual_action_pending", None)
        pending_was_set = bool(pending and pending.is_set())
        if acquired:
            if pending is not None:
                pending.set()
            try:
                try:
                    account, ms = self._timed(self.engine.gateway.fresh_account_state)
                    rows.append(
                        self._check_row(
                            "account",
                            "Счёт и позиции",
                            STATUS_WORKING,
                            f"Баланс ${float(account.get('accountValue') or 0):.2f}; позиций: {len(account.get('positions') or {})}.",
                            ms,
                        )
                    )
                except Exception as exc:
                    rows.append(self._check_row("account", "Счёт и позиции", STATUS_PROBLEM, str(exc)))
                try:
                    orders, ms = self._timed(self.engine.gateway.fresh_open_orders)
                    rows.append(
                        self._check_row(
                            "orders",
                            "Открытые ордера",
                            STATUS_WORKING,
                            f"Биржа вернула {len(orders)} открытых ордеров.",
                            ms,
                        )
                    )
                except Exception as exc:
                    rows.append(self._check_row("orders", "Открытые ордера", STATUS_PROBLEM, str(exc)))
                if isinstance(account, dict) and isinstance(orders, list):
                    status, detail = self._consistency_check(account, orders)
                    rows.append(self._check_row("consistency", "Локальное состояние ↔ биржа", status, detail))
                else:
                    rows.append(
                        self._check_row(
                            "consistency",
                            "Локальное состояние ↔ биржа",
                            STATUS_MANUAL,
                            "Не проверено: сначала нужны успешные чтения счёта и ордеров.",
                        )
                    )
            finally:
                if pending is not None and not pending_was_set:
                    pending.clear()
                action_lock.release()
        else:
            rows.extend(
                [
                    self._check_row(
                        "account",
                        "Счёт и позиции",
                        STATUS_MANUAL,
                        "Не проверено сейчас: LIVE выполняет другую биржевую операцию.",
                    ),
                    self._check_row(
                        "orders",
                        "Открытые ордера",
                        STATUS_MANUAL,
                        "Не проверено сейчас: LIVE выполняет другую биржевую операцию.",
                    ),
                    self._check_row(
                        "consistency",
                        "Локальное состояние ↔ биржа",
                        STATUS_MANUAL,
                        "Не проверено, чтобы не вмешиваться в текущую торговую операцию.",
                    ),
                ]
            )

        research = self._research_snapshot()
        rows.append(
            self._check_row(
                "journal",
                "Локальный research-журнал",
                STATUS_WORKING if research.get("journalWritable") else STATUS_PROBLEM,
                "Каталог доступен для записи." if research.get("journalWritable") else "Research-каталог недоступен для записи.",
            )
        )
        rows.append(
            self._check_row(
                "github-sync",
                "Research → GitHub",
                STATUS_PARTIAL if research.get("syncThreadAlive") else STATUS_PROBLEM,
                (
                    "Scheduler работает, но удалённый push специально не помечается зелёным без отдельного сетевого подтверждения."
                    if research.get("syncThreadAlive")
                    else "Фоновый scheduler research-sync не работает."
                ),
            )
        )

        cluster = research.get("cluster") or {}
        rows.append(
            self._check_row(
                "clusters",
                "Кластерный WebSocket",
                STATUS_WORKING if cluster.get("connected") else STATUS_PARTIAL,
                "Подключён." if cluster.get("connected") else str(cluster.get("lastError") or "Сейчас не подтверждён как подключён."),
            )
        )

        updater = self._updater_snapshot()
        rows.append(
            self._check_row(
                "updater",
                "Updater",
                STATUS_PROBLEM if updater.get("error") else STATUS_PARTIAL if updater.get("worktreeClean") is False else STATUS_WORKING,
                str(updater.get("error") or ("Есть локальные изменения Git." if updater.get("worktreeClean") is False else "Локальное состояние updater корректно; сетевой GitHub fetch выполняется отдельной кнопкой Проверить обновление.")),
            )
        )

        rows.append(
            self._check_row(
                "logic-audit",
                "Соответствие концепции",
                STATUS_PARTIAL if self._logic_audit() else STATUS_WORKING,
                "Автоматически найдены расхождения документации и текущей логики; см. блок «Проверка логики»." if self._logic_audit() else "Явных расхождений не найдено.",
            )
        )

        rank = {STATUS_WORKING: 0, STATUS_MANUAL: 1, STATUS_PARTIAL: 2, STATUS_DISCONNECTED: 2, STATUS_PROBLEM: 3}
        overall = max(rows, key=lambda row: rank.get(str(row.get("status")), 1)).get("status") if rows else STATUS_MANUAL
        payload = {
            "checkedAtMs": self._now_ms(),
            "coin": selected,
            "overall": overall,
            "elapsedMs": round((time.monotonic() - started) * 1000, 1),
            "steps": rows,
            "note": "Проверка read-only: она не снимает SAFE MODE, не меняет кампании и не отправляет торговые команды.",
        }
        setattr(self.engine, "_project_control_center_last_check", payload)
        return payload


def control_center_for(engine: Any) -> ProjectControlCenter:
    center = getattr(engine, "_project_control_center", None)
    if center is None:
        center = ProjectControlCenter(engine)
        setattr(engine, "_project_control_center", center)
    return center

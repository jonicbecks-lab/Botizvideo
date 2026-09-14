from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from .config import load_config
from .engine import ACTIVE_STATUSES, LiveEngineError


UPDATE_BRANCH = "agent/galka-three-live-campaigns"
REMOTE_NAME = "origin"
REMOTE_REF = f"refs/remotes/{REMOTE_NAME}/{UPDATE_BRANCH}"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
UI_PREFIX = "terminal/"


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    os.chmod(path, 0o600)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _run(
    args: list[str],
    *,
    cwd: Path,
    timeout: float = 30.0,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        args,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout,
        check=False,
        env=env,
    )
    if check and result.returncode != 0:
        output = (result.stdout or "").strip()[-2000:]
        raise LiveEngineError(f"Команда завершилась с кодом {result.returncode}: {output or args[0]}")
    return result


def _git(repo_root: Path, *args: str, timeout: float = 30.0, check: bool = True) -> str:
    result = _run(["git", *args], cwd=repo_root, timeout=timeout, check=check)
    return (result.stdout or "").strip()


def _current_sha(repo_root: Path) -> str:
    value = _git(repo_root, "rev-parse", "HEAD")
    if not SHA_RE.fullmatch(value):
        raise LiveEngineError("Не удалось определить текущий commit GALKA")
    return value


def _short(sha: str | None) -> str | None:
    return sha[:7] if sha and SHA_RE.fullmatch(sha) else None


def _changed_files(repo_root: Path, old_sha: str, new_sha: str) -> list[str]:
    if old_sha == new_sha:
        return []
    text = _git(repo_root, "diff", "--name-only", f"{old_sha}..{new_sha}")
    return [row.strip() for row in text.splitlines() if row.strip()]


def _update_type(paths: list[str]) -> str:
    if not paths:
        return "none"
    return "ui" if all(path.startswith(UI_PREFIX) for path in paths) else "backend"


class GalkaUpdateManager:
    """Narrow, fixed-action updater. No user supplied shell commands are accepted."""

    def __init__(self, engine: Any):
        self.engine = engine
        self.repo_root = Path(__file__).resolve().parents[1]
        self.runtime_dir = Path(engine.config.data_dir) / "runtime"
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.status_path = self.runtime_dir / "updater-status.json"
        self.last_success_path = self.runtime_dir / "updater-last-success.json"
        self.operation_lock = self.runtime_dir / "updater-operation.lock"
        self.trade_guard = self.runtime_dir / "updater.active"
        self._lock = threading.RLock()

    def _branch(self) -> str:
        return _git(self.repo_root, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)

    def _dirty(self) -> list[str]:
        text = _git(self.repo_root, "status", "--porcelain")
        return [row for row in text.splitlines() if row.strip()]

    def _remote_sha(self) -> str | None:
        value = _git(self.repo_root, "rev-parse", "--verify", REMOTE_REF, check=False)
        return value if SHA_RE.fullmatch(value) else None

    def _operation_active(self) -> bool:
        if not self.operation_lock.exists():
            return False
        data = _read_json(self.operation_lock) or {}
        pid = int(data.get("pid") or 0)
        started = float(data.get("startedMonotonic") or 0)
        if pid > 0:
            try:
                os.kill(pid, 0)
                return True
            except OSError:
                pass
        if started and time.monotonic() - started < 20:
            return True
        try:
            self.operation_lock.unlink()
        except OSError:
            return True
        return False

    def status(self) -> dict[str, Any]:
        with self._lock:
            local = _current_sha(self.repo_root)
            remote = self._remote_sha()
            paths = _changed_files(self.repo_root, local, remote) if remote else []
            last = _read_json(self.status_path) or {}
            success = _read_json(self.last_success_path)
            active_campaigns: list[dict[str, str]] = []
            with self.engine.lock:
                for coin, campaign in self.engine.state.get("campaigns", {}).items():
                    if isinstance(campaign, dict) and campaign.get("status") in ACTIVE_STATUSES:
                        active_campaigns.append({"coin": str(coin), "status": str(campaign.get("status"))})
                system = deepcopy(self.engine.state.get("system", {}))
            return {
                "branch": self._branch(),
                "targetBranch": UPDATE_BRANCH,
                "installedSha": local,
                "installedShort": _short(local),
                "latestSha": remote,
                "latestShort": _short(remote),
                "available": bool(remote and remote != local),
                "updateType": _update_type(paths),
                "changedFiles": paths[:80],
                "changedFileCount": len(paths),
                "worktreeClean": not self._dirty(),
                "busy": self._operation_active(),
                "tradeGuardActive": self.trade_guard.exists(),
                "activeCampaigns": active_campaigns,
                "safeMode": bool(system.get("safeMode")),
                "safeModeReason": system.get("safeModeReason"),
                "lastOperation": last,
                "rollbackAvailable": bool(success and success.get("fromSha") and success.get("toSha") == local),
                "rollback": success,
            }

    def check(self) -> dict[str, Any]:
        with self._lock:
            if self._operation_active():
                raise LiveEngineError("Обновление GALKA уже выполняется")
            branch = self._branch()
            local = _current_sha(self.repo_root)
            dirty = self._dirty()
            fetch = _run(
                ["git", "fetch", "--quiet", REMOTE_NAME, f"{UPDATE_BRANCH}:{REMOTE_REF}"],
                cwd=self.repo_root,
                timeout=35,
                check=False,
            )
            if fetch.returncode != 0:
                raise LiveEngineError("Не удалось проверить GitHub. Проверь интернет и доступ к origin.")
            remote = self._remote_sha()
            if not remote:
                raise LiveEngineError("GitHub не вернул commit рабочей ветки GALKA")
            ancestor = _run(
                ["git", "merge-base", "--is-ancestor", local, remote],
                cwd=self.repo_root,
                timeout=8,
                check=False,
            ).returncode == 0
            paths = _changed_files(self.repo_root, local, remote)
            result = {
                "checkedAtMs": int(time.time() * 1000),
                "branch": branch,
                "targetBranch": UPDATE_BRANCH,
                "installedSha": local,
                "installedShort": _short(local),
                "latestSha": remote,
                "latestShort": _short(remote),
                "available": remote != local,
                "fastForward": ancestor,
                "worktreeClean": not dirty,
                "dirtyEntries": dirty[:30],
                "updateType": _update_type(paths),
                "changedFiles": paths[:80],
                "changedFileCount": len(paths),
                "blockedReason": (
                    f"Открыта ветка {branch or 'detached HEAD'}, нужна {UPDATE_BRANCH}"
                    if branch != UPDATE_BRANCH
                    else "Есть локальные изменения в Git. Автообновление остановлено."
                    if dirty
                    else "Локальная ветка разошлась с GitHub; fast-forward невозможен."
                    if remote != local and not ancestor
                    else None
                ),
            }
            _atomic_json(self.status_path, {"state": "checked", **result})
            return result

    def _trade_safety(self) -> dict[str, Any]:
        pending = getattr(self.engine, "_manual_action_pending", None)
        acquired = self.engine.action_lock.acquire(timeout=4.0)
        if not acquired:
            return {"safe": False, "reasons": ["LIVE сейчас выполняет биржевую операцию"]}
        if pending is not None:
            pending.set()
        try:
            with self.engine.lock:
                system = deepcopy(self.engine.state.get("system", {}))
                active = [
                    {"coin": str(coin), "status": str(campaign.get("status"))}
                    for coin, campaign in self.engine.state.get("campaigns", {}).items()
                    if isinstance(campaign, dict) and campaign.get("status") in ACTIVE_STATUSES
                ]
            account = self.engine.gateway.fresh_account_state()
            orders = self.engine.gateway.fresh_open_orders()
            positions: list[dict[str, Any]] = []
            for coin, row in (account.get("positions") or {}).items():
                try:
                    size = float((row or {}).get("size") or 0)
                except (TypeError, ValueError):
                    size = 0.0
                if abs(size) > 1e-12:
                    positions.append({"coin": str(coin), "size": size})
            reasons: list[str] = []
            if active:
                reasons.append("Есть активная GALKA")
            if positions:
                reasons.append("На Hyperliquid есть открытая позиция")
            if orders:
                reasons.append("На Hyperliquid есть открытые ордера")
            if system.get("safeMode"):
                reasons.append("Включён SAFE MODE")
            if any(row.get("status") == "recovery" for row in active):
                reasons.append("Есть recovery")
            return {
                "safe": not reasons,
                "reasons": reasons,
                "activeCampaigns": active,
                "positions": positions,
                "openOrderCount": len(orders),
                "safeMode": bool(system.get("safeMode")),
                "safeModeReason": system.get("safeModeReason"),
            }
        finally:
            if pending is not None:
                pending.clear()
            self.engine.action_lock.release()

    def _spawn(self, *, mode: str, target: str, from_sha: str, update_type: str) -> dict[str, Any]:
        if mode not in {"update", "restart", "rollback"}:
            raise LiveEngineError("Недопустимое действие updater")
        if not SHA_RE.fullmatch(target) or not SHA_RE.fullmatch(from_sha):
            raise LiveEngineError("Некорректный commit updater")
        if update_type not in {"ui", "backend", "none"}:
            raise LiveEngineError("Некорректный тип обновления")
        if self._operation_active():
            raise LiveEngineError("Обновление GALKA уже выполняется")

        placeholder = {
            "pid": 0,
            "mode": mode,
            "startedAtMs": int(time.time() * 1000),
            "startedMonotonic": time.monotonic(),
        }
        _atomic_json(self.operation_lock, placeholder)
        guard_required = update_type == "backend" or mode == "restart"
        if guard_required:
            _atomic_json(
                self.trade_guard,
                {"mode": mode, "fromSha": from_sha, "targetSha": target, "startedAtMs": int(time.time() * 1000)},
            )
        try:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "live.update_manager",
                    "--worker",
                    "--mode",
                    mode,
                    "--target",
                    target,
                    "--from-sha",
                    from_sha,
                    "--update-type",
                    update_type,
                ],
                cwd=self.repo_root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                close_fds=True,
                env=os.environ.copy(),
            )
            placeholder["pid"] = process.pid
            _atomic_json(self.operation_lock, placeholder)
        except Exception:
            for path in (self.operation_lock, self.trade_guard):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
            raise
        return {
            "started": True,
            "mode": mode,
            "pid": process.pid,
            "fromSha": from_sha,
            "fromShort": _short(from_sha),
            "targetSha": target,
            "targetShort": _short(target),
            "updateType": update_type,
            "requiresRestart": guard_required,
        }

    def install(self, confirmation: str) -> dict[str, Any]:
        if confirmation != "INSTALL_GALKA_UPDATE":
            raise LiveEngineError("Не подтверждена установка обновления GALKA")
        with self._lock:
            check = self.check()
            if check.get("blockedReason"):
                raise LiveEngineError(str(check["blockedReason"]))
            if not check.get("available"):
                return {"started": False, "message": "Установлена последняя версия", **check}
            update_type = str(check.get("updateType") or "backend")
            safety = None
            if update_type == "backend":
                safety = self._trade_safety()
                if not safety.get("safe"):
                    raise LiveEngineError("Backend-обновление заблокировано: " + "; ".join(safety.get("reasons") or []))
            result = self._spawn(
                mode="update",
                target=str(check["latestSha"]),
                from_sha=str(check["installedSha"]),
                update_type=update_type,
            )
            result["safety"] = safety
            return result

    def restart(self, confirmation: str) -> dict[str, Any]:
        if confirmation != "RESTART_GALKA":
            raise LiveEngineError("Не подтверждён перезапуск GALKA")
        with self._lock:
            safety = self._trade_safety()
            if not safety.get("safe"):
                raise LiveEngineError("Перезапуск заблокирован: " + "; ".join(safety.get("reasons") or []))
            local = _current_sha(self.repo_root)
            result = self._spawn(mode="restart", target=local, from_sha=local, update_type="backend")
            result["safety"] = safety
            return result

    def rollback(self, confirmation: str) -> dict[str, Any]:
        if confirmation != "ROLLBACK_GALKA_UPDATE":
            raise LiveEngineError("Не подтверждён откат GALKA")
        with self._lock:
            success = _read_json(self.last_success_path)
            if not success:
                raise LiveEngineError("Нет последнего обновления, которое можно откатить")
            local = _current_sha(self.repo_root)
            if str(success.get("toSha") or "") != local:
                raise LiveEngineError("Текущий commit уже изменился; автоматический откат последнего обновления запрещён")
            target = str(success.get("fromSha") or "")
            update_type = str(success.get("updateType") or "backend")
            if not SHA_RE.fullmatch(target):
                raise LiveEngineError("Повреждены данные последнего обновления")
            safety = None
            if update_type == "backend":
                safety = self._trade_safety()
                if not safety.get("safe"):
                    raise LiveEngineError("Откат backend заблокирован: " + "; ".join(safety.get("reasons") or []))
            result = self._spawn(
                mode="rollback",
                target=target,
                from_sha=local,
                update_type=update_type,
            )
            result["safety"] = safety
            return result


def manager_for(engine: Any) -> GalkaUpdateManager:
    manager = getattr(engine, "_galka_update_manager", None)
    if manager is None:
        manager = GalkaUpdateManager(engine)
        setattr(engine, "_galka_update_manager", manager)
    return manager


class _Worker:
    def __init__(self, mode: str, target: str, from_sha: str, update_type: str):
        self.mode = mode
        self.target = target
        self.from_sha = from_sha
        self.update_type = update_type
        self.repo_root = Path(__file__).resolve().parents[1]
        self.config = load_config()
        self.runtime_dir = Path(self.config.data_dir) / "runtime"
        self.status_path = self.runtime_dir / "updater-status.json"
        self.last_success_path = self.runtime_dir / "updater-last-success.json"
        self.operation_lock = self.runtime_dir / "updater-operation.lock"
        self.trade_guard = self.runtime_dir / "updater.active"
        self.pid_file = self.runtime_dir / "server.pid"
        self.token_file = self.runtime_dir / "browser-session.token"
        self.steps: list[dict[str, Any]] = []
        self.started = time.monotonic()
        self.backup_dir: Path | None = None

    def _status(self, state: str, **extra: Any) -> None:
        payload = {
            "state": state,
            "mode": self.mode,
            "updateType": self.update_type,
            "fromSha": self.from_sha,
            "fromShort": _short(self.from_sha),
            "targetSha": self.target,
            "targetShort": _short(self.target),
            "steps": deepcopy(self.steps),
            "elapsedMs": round((time.monotonic() - self.started) * 1000),
            "updatedAtMs": int(time.time() * 1000),
            **extra,
        }
        _atomic_json(self.status_path, payload)

    def _step(self, name: str, action: Callable[[], Any]) -> Any:
        row = {"name": name, "state": "running", "startedAtMs": int(time.time() * 1000)}
        self.steps.append(row)
        self._status("running")
        started = time.monotonic()
        try:
            result = action()
            row["state"] = "ok"
            row["ms"] = round((time.monotonic() - started) * 1000)
            self._status("running")
            return result
        except Exception as exc:
            row["state"] = "error"
            row["ms"] = round((time.monotonic() - started) * 1000)
            row["error"] = str(exc)[:1000]
            self._status("running")
            raise

    def _fetch_target(self) -> None:
        _run(
            ["git", "fetch", "--quiet", REMOTE_NAME, f"{UPDATE_BRANCH}:{REMOTE_REF}"],
            cwd=self.repo_root,
            timeout=40,
        )
        remote = _git(self.repo_root, "rev-parse", "--verify", REMOTE_REF)
        if self.mode == "update" and remote != self.target:
            # Install exactly the commit the user approved. It only needs to remain
            # reachable from the same remote branch if a newer patch arrived meanwhile.
            ancestry = _run(
                ["git", "merge-base", "--is-ancestor", self.target, remote],
                cwd=self.repo_root,
                timeout=8,
                check=False,
            )
            if ancestry.returncode != 0:
                raise LiveEngineError("Одобренный commit больше не принадлежит рабочей ветке GitHub")

    def _backup_state(self) -> None:
        stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        self.backup_dir = self.runtime_dir / "updater-backups" / f"{stamp}-{_short(self.from_sha)}"
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.backup_dir, 0o700)
        for name in ("state.json", "state.prev.json"):
            source = Path(self.config.data_dir) / name
            if source.exists() and not source.is_symlink():
                shutil.copy2(source, self.backup_dir / name)
                os.chmod(self.backup_dir / name, 0o600)
        _atomic_json(
            self.backup_dir / "update.json",
            {"fromSha": self.from_sha, "targetSha": self.target, "mode": self.mode, "updateType": self.update_type},
        )

    def _restore_state_backup(self) -> None:
        if not self.backup_dir:
            return
        for name in ("state.json", "state.prev.json"):
            source = self.backup_dir / name
            if source.exists():
                destination = Path(self.config.data_dir) / name
                shutil.copy2(source, destination)
                os.chmod(destination, 0o600)

    def _apply_target(self) -> None:
        if self.mode == "update":
            current = _current_sha(self.repo_root)
            if current != self.from_sha:
                raise LiveEngineError("Commit изменился после подтверждения; обновление остановлено")
            ff = _run(
                ["git", "merge-base", "--is-ancestor", current, self.target],
                cwd=self.repo_root,
                timeout=8,
                check=False,
            )
            if ff.returncode != 0:
                raise LiveEngineError("Обновление больше не является fast-forward")
            _git(self.repo_root, "merge", "--ff-only", self.target, timeout=45)
        elif self.mode == "rollback":
            current = _current_sha(self.repo_root)
            if current != self.from_sha:
                raise LiveEngineError("Commit изменился после подтверждения отката")
            _git(self.repo_root, "reset", "--hard", self.target, timeout=30)

    def _quick_tests(self) -> None:
        python = self.repo_root / ".venv-live" / "bin" / "python"
        if not python.exists():
            raise LiveEngineError("Не найден Python GALKA LIVE")
        _run([str(python), "-m", "compileall", "-q", "live"], cwd=self.repo_root, timeout=30)
        _run(
            [str(python), "-c", "import live.research_server, live.research_v3_engine"],
            cwd=self.repo_root,
            timeout=20,
        )
        node = shutil.which("node")
        checker = self.repo_root / "scripts" / "check-live-terminal.mjs"
        if node and checker.exists():
            _run([node, str(checker)], cwd=self.repo_root, timeout=30)
        if node:
            changed = _changed_files(self.repo_root, self.from_sha, self.target)
            for path in changed:
                if path.startswith("terminal/") and path.endswith(".js"):
                    _run([node, "--check", path], cwd=self.repo_root, timeout=15)

    def _server_pid(self) -> int:
        try:
            pid = int(self.pid_file.read_text(encoding="utf-8").strip())
        except (FileNotFoundError, ValueError, OSError):
            return 0
        return pid if pid > 1 else 0

    def _stop_server(self) -> None:
        pid = self._server_pid()
        if not pid:
            return
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except OSError:
                break
            time.sleep(0.15)
        else:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        try:
            self.pid_file.unlink()
        except FileNotFoundError:
            pass

    def _start_server(self) -> None:
        env = os.environ.copy()
        env["GALKA_LIVE_NO_OPEN"] = "1"
        result = _run(
            ["bash", "scripts/galka-live-start.sh"],
            cwd=self.repo_root,
            timeout=35,
            check=False,
            env=env,
        )
        if result.returncode != 0:
            raise LiveEngineError("GALKA не запустилась после обновления: " + (result.stdout or "")[-1600:])

    def _verify_server(self) -> None:
        base = f"http://{self.config.host}:{self.config.port}"
        last_error = "server unavailable"
        for _ in range(30):
            try:
                with urllib.request.urlopen(f"{base}/healthz", timeout=1.0) as response:
                    health = json.load(response)
                if health.get("server") != "galka-live":
                    raise RuntimeError("wrong health payload")
                token = self.token_file.read_text(encoding="utf-8").strip()
                request = urllib.request.Request(
                    f"{base}/api/live/status",
                    headers={"X-Galka-Session": token},
                )
                with urllib.request.urlopen(request, timeout=2.0) as response:
                    payload = json.load(response)
                data = payload.get("data") if isinstance(payload, dict) else None
                if not isinstance(data, dict):
                    raise RuntimeError("status unavailable")
                system = data.get("system") or {}
                if not system.get("monitorAlive"):
                    raise RuntimeError("LIVE monitor is not alive")
                if system.get("safeMode"):
                    raise RuntimeError("SAFE MODE after restart: " + str(system.get("safeModeReason") or "unknown"))
                if not isinstance(data.get("accountState"), dict) or not isinstance(data.get("mids"), dict):
                    raise RuntimeError("Hyperliquid state unavailable")
                return
            except Exception as exc:
                last_error = str(exc)
                time.sleep(0.35)
        raise LiveEngineError("Проверка LIVE/Hyperliquid не пройдена: " + last_error)

    def _record_success(self, installed_sha: str) -> None:
        if self.mode == "rollback":
            try:
                self.last_success_path.unlink()
            except FileNotFoundError:
                pass
            return
        if self.mode != "update":
            return
        _atomic_json(
            self.last_success_path,
            {
                "fromSha": self.from_sha,
                "fromShort": _short(self.from_sha),
                "toSha": installed_sha,
                "toShort": _short(installed_sha),
                "updateType": self.update_type,
                "completedAtMs": int(time.time() * 1000),
            },
        )

    def _cleanup(self) -> None:
        for path in (self.operation_lock, self.trade_guard):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass

    def _automatic_rollback(self, original_error: Exception) -> None:
        rollback_error: Exception | None = None
        try:
            self._status("rolling_back", error=str(original_error)[:1200])
            self._stop_server()
            _git(self.repo_root, "reset", "--hard", self.from_sha, timeout=30)
            if self.mode == "update" and self.update_type == "backend":
                self._restore_state_backup()
            if self.update_type == "backend" or self.mode == "restart":
                self._start_server()
                self._verify_server()
        except Exception as exc:
            rollback_error = exc
        if rollback_error is None:
            self._status(
                "failed_rolled_back",
                ok=False,
                error=str(original_error)[:1200],
                installedSha=_current_sha(self.repo_root),
                installedShort=_short(_current_sha(self.repo_root)),
                rolledBack=True,
            )
        else:
            self._status(
                "failed_rollback_failed",
                ok=False,
                error=str(original_error)[:1200],
                rollbackError=str(rollback_error)[:1200],
                rolledBack=False,
            )

    def run(self) -> int:
        self._status("starting")
        try:
            if self.mode in {"update", "rollback"}:
                self._step("GitHub: проверка commit", self._fetch_target)
            if self.update_type == "backend" and self.mode == "update":
                self._step("State: резервная копия", self._backup_state)
            if self.mode in {"update", "rollback"}:
                self._step("Файлы: установка", self._apply_target)
                self._step("Тесты: быстрые проверки", self._quick_tests)
            if self.update_type == "backend" or self.mode == "restart":
                self._step("GALKA: остановка", self._stop_server)
                self._step("GALKA: запуск", self._start_server)
                self._step("LIVE + Hyperliquid: проверка", self._verify_server)
            installed = _current_sha(self.repo_root)
            self._record_success(installed)
            self._status(
                "success",
                ok=True,
                installedSha=installed,
                installedShort=_short(installed),
                requiresReload=self.mode in {"update", "rollback"},
                message=(
                    "UI обновлён; перезагрузи страницу"
                    if self.update_type == "ui" and self.mode != "restart"
                    else "GALKA обновлена и проверена"
                    if self.mode == "update"
                    else "GALKA перезапущена и проверена"
                    if self.mode == "restart"
                    else "Последнее обновление откачено"
                ),
            )
            return 0
        except Exception as exc:
            if self.mode == "restart":
                self._status("failed", ok=False, error=str(exc)[:1200])
            else:
                self._automatic_rollback(exc)
            return 1
        finally:
            self._cleanup()


def _worker_cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--mode", choices=["update", "restart", "rollback"], required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--from-sha", required=True)
    parser.add_argument("--update-type", choices=["ui", "backend", "none"], required=True)
    args = parser.parse_args(argv)
    if not args.worker or not SHA_RE.fullmatch(args.target) or not SHA_RE.fullmatch(args.from_sha):
        return 2
    return _Worker(args.mode, args.target, args.from_sha, args.update_type).run()


if __name__ == "__main__":
    raise SystemExit(_worker_cli(sys.argv[1:]))

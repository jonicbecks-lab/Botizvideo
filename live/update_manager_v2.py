from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from typing import Any

from .engine import LiveEngineError
from .update_manager import (
    SHA_RE,
    GalkaUpdateManager as _BaseManager,
    _Worker as _BaseWorker,
    _atomic_json,
    _current_sha,
    _git,
    _short,
)


class GalkaUpdateManager(_BaseManager):
    """Use the hardened worker while preserving the fixed updater API."""

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
                {
                    "mode": mode,
                    "fromSha": from_sha,
                    "targetSha": target,
                    "startedAtMs": int(time.time() * 1000),
                },
            )
        try:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "live.update_manager_v2",
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


class _Worker(_BaseWorker):
    def _automatic_rollback(self, original_error: Exception) -> None:
        rollback_error: Exception | None = None
        restart_required = self.update_type == "backend" or self.mode == "restart"
        try:
            self._status("rolling_back", error=str(original_error)[:1200])
            if restart_required:
                self._stop_server()
            if self.mode in {"update", "rollback"}:
                _git(self.repo_root, "reset", "--hard", self.from_sha, timeout=30)
            if self.mode == "update" and self.update_type == "backend":
                self._restore_state_backup()
            if restart_required:
                self._start_server()
                self._verify_server()
        except Exception as exc:
            rollback_error = exc
        if rollback_error is None:
            installed = _current_sha(self.repo_root)
            self._status(
                "failed_rolled_back",
                ok=False,
                error=str(original_error)[:1200],
                installedSha=installed,
                installedShort=_short(installed),
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
            # Rollback is intentionally offline-capable: the previous commit is
            # already in the local repository and must not depend on GitHub being up.
            if self.mode == "update":
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
                    "UI обновлён; страница будет перезагружена"
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


def manager_for(engine: Any) -> GalkaUpdateManager:
    manager = getattr(engine, "_galka_update_manager_v2", None)
    if manager is None:
        manager = GalkaUpdateManager(engine)
        setattr(engine, "_galka_update_manager_v2", manager)
    return manager


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

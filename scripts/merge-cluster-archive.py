#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


def version(row: dict[str, Any]) -> tuple[int, int, float]:
    return (
        int(row.get("l") or 0),
        int(row.get("n") or 0),
        float(row.get("q") or 0),
    )


def load(path: Path) -> dict[tuple[int, int], dict[str, Any]]:
    rows: dict[tuple[int, int], dict[str, Any]] = {}
    if not path.is_file() or path.is_symlink():
        return rows
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if not isinstance(row, dict):
                    continue
                try:
                    key = (int(row.get("t") or 0), int(row.get("p") or 0))
                except (TypeError, ValueError):
                    continue
                if key[0] <= 0 or key[1] < 0:
                    continue
                previous = rows.get(key)
                if previous is None or version(row) > version(previous):
                    rows[key] = row
    except OSError:
        return {}
    return rows


def write(path: Path, rows: dict[tuple[int, int], dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".tmp-{os.getpid()}")
    with temporary.open("w", encoding="utf-8") as handle:
        for key in sorted(rows):
            handle.write(json.dumps(rows[key], separators=(",", ":"), allow_nan=False))
            handle.write("\n")
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    os.replace(temporary, path)


def merge_roots(local_root: Path, git_root: Path) -> None:
    local_root.mkdir(parents=True, exist_ok=True)
    git_root.mkdir(parents=True, exist_ok=True)
    relative_paths = {
        path.relative_to(local_root)
        for path in local_root.glob("*/*.jsonl")
        if path.is_file() and not path.is_symlink()
    }
    relative_paths.update(
        path.relative_to(git_root)
        for path in git_root.glob("*/*.jsonl")
        if path.is_file() and not path.is_symlink()
    )

    for relative in sorted(relative_paths):
        local_path = local_root / relative
        git_path = git_root / relative
        merged = load(git_path)
        for key, row in load(local_path).items():
            previous = merged.get(key)
            if previous is None or version(row) > version(previous):
                merged[key] = row
        if not merged:
            continue
        write(git_path, merged)
        write(local_path, merged)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: merge-cluster-archive.py LOCAL_ROOT GIT_ROOT")
    merge_roots(Path(sys.argv[1]), Path(sys.argv[2]))

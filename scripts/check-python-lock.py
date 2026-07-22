#!/usr/bin/env python3
from __future__ import annotations

import re
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, distributions, version
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "live" / "requirements-termux.txt"
TOOLING = {
    "packaging": "26.2",
    "pip": "26.1.2",
    "setuptools": "83.0.0",
    "wheel": "0.47.0",
}


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def main() -> int:
    expected: dict[str, str] = {}
    for raw_line in LOCK.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.count("==") != 1:
            raise SystemExit(f"Unpinned requirement: {line}")
        package, wanted = line.split("==", 1)
        key = canonical(package)
        if key in expected:
            raise SystemExit(f"Duplicate locked package: {package}")
        expected[key] = wanted

    failures: list[str] = []
    allowed = {**expected, **TOOLING}
    for package, wanted in allowed.items():
        try:
            actual = version(package)
        except PackageNotFoundError:
            failures.append(f"{package}: missing")
            continue
        if actual != wanted:
            failures.append(f"{package}: installed {actual}, locked {wanted}")
    installed: dict[str, str] = {}
    for distribution in distributions():
        package = distribution.metadata.get("Name")
        if not package:
            failures.append("installed distribution without a package name")
            continue
        key = canonical(package)
        if key in installed:
            failures.append(f"{key}: duplicate installed distributions")
        installed[key] = distribution.version
    unexpected = sorted(set(installed) - set(allowed))
    failures.extend(f"{package}: unexpected package {installed[package]}" for package in unexpected)
    if failures:
        raise SystemExit("Python lock mismatch:\n" + "\n".join(f"- {item}" for item in failures))
    subprocess.run([sys.executable, "-m", "pip", "check"], check=True)
    try:
        version("pydantic-core")
    except PackageNotFoundError:
        pass
    else:
        raise SystemExit("pydantic-core must not be installed in the Termux LIVE environment")
    print(f"Python lock verified: {len(expected)} runtime packages + {len(TOOLING)} pinned tooling packages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Verify that the research environment is exactly the reviewed dependency closure."""

from __future__ import annotations

import re
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, distributions, version
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "research" / "requirements.txt"
ALLOWED_TOOLING = {"pip", "setuptools", "wheel"}


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def main() -> int:
    expected: dict[str, str] = {}
    for raw_line in LOCK.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.count("==") != 1:
            raise SystemExit(f"Unpinned research requirement: {line}")
        package, wanted = line.split("==", 1)
        key = canonical(package)
        if not package or not wanted or key in expected:
            raise SystemExit(f"Invalid or duplicate research requirement: {line}")
        expected[key] = wanted

    failures: list[str] = []
    for package, wanted in expected.items():
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
    unexpected = sorted(set(installed) - set(expected) - ALLOWED_TOOLING)
    failures.extend(f"{package}: unexpected package {installed[package]}" for package in unexpected)
    if failures:
        raise SystemExit("Research lock mismatch:\n" + "\n".join(f"- {item}" for item in failures))
    subprocess.run([sys.executable, "-m", "pip", "check"], check=True)
    print(f"Research lock verified: {len(expected)} packages; no unpinned runtime dependencies")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

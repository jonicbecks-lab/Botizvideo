#!/usr/bin/env python3
"""Static security and coverage contract for repository GitHub workflows."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = ROOT / ".github" / "workflows"
EXPECTED = {
    "ci.yml",
    "galka-final-integration.yml",
    "galka-lab-full.yml",
    "galka-live-hyperliquid.yml",
    "galka-visual-regression.yml",
    "rebuild-btc-history.yml",
    "reclaim-backtest.yml",
    "research.yml",
}
PINNED_ACTIONS = {
    "actions/checkout": "11d5960a326750d5838078e36cf38b85af677262",
    "actions/setup-node": "49933ea5288caeca8642d1e84afbd3f7d6820020",
    "actions/setup-python": "a26af69be951a213d495a4c3e4e4022e16d87065",
    "actions/upload-artifact": "ea165f8d65b6e75b540449e92b4886f43607fa02",
    "actions/download-artifact": "d3f86a106a0bac45b974a628896c90dbdf5c8093",
    "actions/cache": "0057852bfaa89a56745cba8c7296529d2fc39830",
}


def top_level_block(text: str, key: str) -> str:
    lines = text.splitlines()
    start = next((index for index, line in enumerate(lines) if line == f"{key}:"), None)
    if start is None:
        return ""
    output = [lines[start]]
    for line in lines[start + 1 :]:
        if line and not line.startswith((" ", "\t", "#")):
            break
        output.append(line)
    return "\n".join(output)


def fail(message: str, failures: list[str]) -> None:
    failures.append(message)


def main() -> int:
    files = sorted(WORKFLOW_DIR.glob("*.yml"))
    failures: list[str] = []
    names = {path.name for path in files}
    if names != EXPECTED:
        fail(f"workflow inventory mismatch: missing={sorted(EXPECTED - names)} extra={sorted(names - EXPECTED)}", failures)

    for path in files:
        text = path.read_text(encoding="utf-8")
        event_block = top_level_block(text, "on")
        permissions = top_level_block(text, "permissions")
        if not permissions:
            fail(f"{path.name}: missing explicit top-level permissions", failures)
        if "pull_request_target" in text:
            fail(f"{path.name}: pull_request_target is forbidden", failures)
        if "${{ secrets." in text:
            fail(f"{path.name}: repository secrets must not enter these workflows", failures)
        if "concurrency:" not in text:
            fail(f"{path.name}: missing concurrency policy", failures)
        if text.count("runs-on:") != text.count("timeout-minutes:"):
            fail(f"{path.name}: every job must have a timeout", failures)

        for match in re.finditer(r"(?m)^\s*-\s+uses:\s+([^\s#]+)", text):
            reference = match.group(1)
            if reference.startswith("./"):
                continue
            action, separator, revision = reference.partition("@")
            if not separator or not re.fullmatch(r"[0-9a-f]{40}", revision):
                fail(f"{path.name}: action is not pinned by full commit: {reference}", failures)
                continue
            expected_revision = PINNED_ACTIONS.get(action)
            if expected_revision is None:
                fail(f"{path.name}: action is not allowlisted: {action}", failures)
            elif revision != expected_revision:
                fail(f"{path.name}: unexpected pin for {action}", failures)

        if "contents: write" in permissions:
            if "pull_request:" in event_block:
                fail(f"{path.name}: write token must not be available to pull requests", failures)
            if "branches: [main]" not in event_block or "github.repository == 'jonicbecks-lab/Botizvideo'" not in text:
                fail(f"{path.name}: top-level write token is not constrained to trusted main", failures)
        if "contents: write" in text and "contents: write" not in permissions:
            if (
                "pull_request:" in event_block
                or "github.repository == 'jonicbecks-lab/Botizvideo'" not in text
                or "github.ref_name == 'agent/galka-statistics-engine'" not in text
            ):
                fail(f"{path.name}: job-level write token lacks a trusted-event guard", failures)
        if re.search(r"git push[^\n]*main", text):
            if "branches: [main]" not in event_block or "github.repository == 'jonicbecks-lab/Botizvideo'" not in text:
                fail(f"{path.name}: direct main push is not constrained", failures)

        try:
            import yaml  # type: ignore
        except ImportError:
            pass
        else:
            try:
                yaml.load(text, Loader=yaml.BaseLoader)
            except Exception as exc:
                fail(f"{path.name}: YAML parse failed: {type(exc).__name__}", failures)

    live = (WORKFLOW_DIR / "galka-live-hyperliquid.yml").read_text(encoding="utf-8")
    for contract in (
        "agent/galka-live-hardening-v3",
        "packaging==26.2 pip==26.1.2 setuptools==83.0.0 wheel==0.47.0",
        "python -m unittest discover -s tests -v",
        "scripts/check-python-lock.py",
        "scripts/check-repository-secrets.py --history",
        "scripts/test-installer-rollback.sh",
        "scripts/test-termux-sync-and-prepare-galka.sh",
    ):
        if contract not in live:
            fail(f"LIVE workflow missing contract: {contract}", failures)

    ci = (WORKFLOW_DIR / "ci.yml").read_text(encoding="utf-8")
    for contract in (
        "npm run check",
        "npm run research:test",
        "scripts/check-research-lock.py",
        "scripts/check-workflows.py",
        "shellcheck -x",
        "scripts/test-termux-sync-and-prepare-galka.sh",
        "actionlint_1.7.12_linux_amd64.tar.gz",
        "8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8",
    ):
        if contract not in ci:
            fail(f"CI workflow missing contract: {contract}", failures)

    for workflow in (
        "ci.yml",
        "galka-lab-full.yml",
        "rebuild-btc-history.yml",
        "reclaim-backtest.yml",
        "research.yml",
    ):
        if "scripts/check-research-lock.py" not in (WORKFLOW_DIR / workflow).read_text(encoding="utf-8"):
            fail(f"{workflow}: research environment is not checked against the full lock", failures)

    lab = (WORKFLOW_DIR / "galka-lab-full.yml").read_text(encoding="utf-8")
    if "pull_request:" in top_level_block(lab, "on"):
        fail("full historical dataset must not run from untrusted pull requests", failures)
    if "needs: dataset" not in lab or "actions/download-artifact@" not in lab:
        fail("Galka Lab publishing must consume verified artifacts in an isolated write job", failures)
    if "github.ref_name == 'agent/galka-statistics-engine'" not in lab:
        fail("Galka Lab publishing must be restricted to its non-production research branch", failures)

    rebuild = (WORKFLOW_DIR / "rebuild-btc-history.yml").read_text(encoding="utf-8")
    if (
        "contents: write" in rebuild
        or "git push" in rebuild
        or "actions/upload-artifact@" not in rebuild
        or "workflow_dispatch:" not in top_level_block(rebuild, "on")
    ):
        fail("BTC history rebuild must be manual, read-only, and artifact-only", failures)

    visual = (WORKFLOW_DIR / "galka-visual-regression.yml").read_text(encoding="utf-8")
    if (
        "lightweight-charts@" in visual
        or "test-paper-recovery-browser.mjs" not in visual
        or "test-legacy-viewers-browser.mjs" not in visual
        or "npm ci --ignore-scripts" not in visual
        or "npx --no-install playwright" not in visual
        or "npm install --no-save" in visual
    ):
        fail("visual workflow must use locked runtimes and both browser recovery/security gates", failures)

    if (WORKFLOW_DIR / "apply-reclaim-trailing.yml").exists() or (ROOT / "scripts" / "apply_reclaim_trail.py").exists():
        fail("obsolete main-mutating reclaim patch automation still exists", failures)

    if failures:
        raise SystemExit("WORKFLOW AUDIT FAILED\n" + "\n".join(f"- {item}" for item in failures))
    print(f"WORKFLOW AUDIT PASS: {len(files)} workflows, all actions immutable and permissions bounded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

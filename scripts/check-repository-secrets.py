#!/usr/bin/env python3
"""Fail closed when private credentials are present in Git content.

The scanner intentionally reports only the file and rule name. It never prints
the matching value, which keeps CI logs safe even when it catches a secret.
"""

from __future__ import annotations

import argparse
import math
import re
import subprocess
import sys
from pathlib import Path


MAX_BLOB_BYTES = 2_000_000
PRIVATE_FILE_RE = re.compile(
    r"(^|/)(?:id_(?:rsa|dsa|ecdsa|ed25519)|[^/]+\.(?:pem|key|p12|pfx|keystore)|credentials(?:\.[^/]+)?|secrets?(?:\.[^/]+)?)$",
    re.IGNORECASE,
)
ENV_FILE_RE = re.compile(r"(^|/)(?:\.env|[^/]+\.env)$", re.IGNORECASE)


def git(*args: str, input_bytes: bytes | None = None, check: bool = True) -> bytes:
    result = subprocess.run(
        ["git", *args],
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode:
        message = result.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(message or f"git {' '.join(args)} failed")
    return result.stdout


def text_patterns() -> list[tuple[str, re.Pattern[str]]]:
    # Split well-known prefixes so this scanner does not flag its own source.
    definitions = [
        ("private-key-block", r"-----BEGIN " + r"(?:[A-Z0-9 ]+ )?PRIVATE KEY-----"),
        ("github-token", r"(?:gh" + r"[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
        ("aws-access-key", r"(?:AK" + r"IA|ASIA)[0-9A-Z]{16}"),
        ("slack-token", r"xo" + r"x[baprs]-[A-Za-z0-9-]{10,}"),
        ("openai-key", r"sk" + r"-(?:proj-)?[A-Za-z0-9_-]{20,}"),
    ]
    return [(name, re.compile(pattern)) for name, pattern in definitions]


HEX_SECRET_RE = re.compile(r"0x" + r"[0-9a-fA-F]{64}")
ASSIGNMENT_RE = re.compile(
    r"(?im)^\s*(?:HL_" + r"API_SECRET_KEY|PRIVATE_KEY|SECRET_KEY)\s*=\s*([^\s#]+)"
)


def looks_like_fixture(value: str) -> bool:
    body = value[2:] if value.lower().startswith("0x") else value
    return len(body) >= 16 and len(set(body.lower())) == 1


def looks_like_placeholder(value: str) -> bool:
    upper = value.upper()
    return (
        len(value) < 32
        or value.startswith(("<", "[", "${"))
        or any(marker in upper for marker in ("PASTE_", "REPLACE_", "EXAMPLE", "NOT_SET"))
    )


def entropy_bits_per_character(value: str) -> float:
    if not value:
        return 0.0
    counts = {character: value.count(character) for character in set(value)}
    return -sum((count / len(value)) * math.log2(count / len(value)) for count in counts.values())


def scan_blob(path: str, blob: bytes) -> list[str]:
    if len(blob) > MAX_BLOB_BYTES or b"\0" in blob:
        return []
    text = blob.decode("utf-8", "replace")
    findings: list[str] = []
    for name, pattern in text_patterns():
        if pattern.search(text):
            findings.append(name)
    for match in HEX_SECRET_RE.finditer(text):
        value = match.group(0)
        if not looks_like_fixture(value) and entropy_bits_per_character(value[2:].lower()) > 2.5:
            findings.append("64-hex-private-key")
            break
    for match in ASSIGNMENT_RE.finditer(text):
        value = match.group(1).strip("\"'")
        if not looks_like_placeholder(value) and not looks_like_fixture(value):
            findings.append("secret-assignment")
            break
    return sorted(set(findings))


def safe_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    if normalized.endswith(".env.example"):
        return True
    return not (PRIVATE_FILE_RE.search(normalized) or ENV_FILE_RE.search(normalized))


def tracked_blobs(staged: bool) -> list[tuple[str, bytes]]:
    if staged:
        raw = git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z")
        paths = [item.decode("utf-8", "surrogateescape") for item in raw.split(b"\0") if item]
        return [(path, git("show", f":{path}")) for path in paths]
    raw = git("ls-files", "-z")
    paths = [item.decode("utf-8", "surrogateescape") for item in raw.split(b"\0") if item]
    result: list[tuple[str, bytes]] = []
    for path in paths:
        try:
            result.append((path, Path(path).read_bytes()))
        except FileNotFoundError:
            continue
    return result


def history_blobs() -> list[tuple[str, bytes]]:
    objects = git("rev-list", "--objects", "--all").decode("utf-8", "replace").splitlines()
    seen: set[str] = set()
    result: list[tuple[str, bytes]] = []
    for row in objects:
        object_id, _, path = row.partition(" ")
        if not path or object_id in seen:
            continue
        seen.add(object_id)
        if git("cat-file", "-t", object_id).strip() != b"blob":
            continue
        size = int(git("cat-file", "-s", object_id))
        if size > MAX_BLOB_BYTES:
            continue
        result.append((f"history:{path}@{object_id[:12]}", git("cat-file", "blob", object_id)))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--staged", action="store_true", help="scan the staged snapshot")
    parser.add_argument("--history", action="store_true", help="scan every reachable Git blob")
    args = parser.parse_args()

    findings: list[tuple[str, str]] = []
    blobs = tracked_blobs(args.staged)
    if args.history:
        blobs.extend(history_blobs())
    for path, blob in blobs:
        display_path = path.split("@", 1)[0].removeprefix("history:")
        if not safe_path(display_path):
            findings.append((path, "private-filename"))
        findings.extend((path, rule) for rule in scan_blob(path, blob))

    if findings:
        print("SECRET SCAN FAILED", file=sys.stderr)
        for path, rule in sorted(set(findings)):
            print(f"- {path}: {rule}", file=sys.stderr)
        print("Matched values are intentionally omitted from this log.", file=sys.stderr)
        return 1
    scope = "staged content" if args.staged else "tracked content"
    if args.history:
        scope += " and reachable history"
    print(f"SECRET SCAN PASS: {scope}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

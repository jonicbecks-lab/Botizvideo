from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO, Iterator

import numpy as np
import pandas as pd


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def frame_hash(frame: pd.DataFrame) -> str:
    columns = ("time", "open", "high", "low", "close", "volume")
    digest = hashlib.sha256()
    time_values = pd.to_datetime(frame["time"], utc=True).astype("int64").to_numpy(dtype="<i8")
    digest.update(np.ascontiguousarray(time_values).tobytes())
    for column in columns[1:]:
        values = frame[column].to_numpy(dtype="<f8", copy=False)
        digest.update(np.ascontiguousarray(values).tobytes())
    return digest.hexdigest()


def _sync_directory(path: Path) -> None:
    """Durably persist a rename on filesystems that support directory fsync."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def atomic_binary_writer(path: Path) -> Iterator[BinaryIO]:
    """Write a file beside its destination and atomically replace it after fsync."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            yield stream
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def write_bytes_atomic(path: Path, value: bytes) -> None:
    with atomic_binary_writer(path) as stream:
        stream.write(value)


def write_gzip_csv(path: Path, frame: pd.DataFrame) -> None:
    """Write a reproducible gzip CSV without filename or wall-clock headers."""
    with atomic_binary_writer(path) as raw:
        with gzip.GzipFile(
            filename="",
            fileobj=raw,
            mode="wb",
            compresslevel=9,
            mtime=0,
        ) as compressed:
            with io.TextIOWrapper(compressed, encoding="utf-8", newline="") as text:
                frame.to_csv(text, index=False)


def iso_utc(value: Any) -> str:
    return pd.Timestamp(value).tz_convert("UTC").isoformat().replace("+00:00", "Z")


def safe_float(value: Any, fallback: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if np.isfinite(parsed) else fallback


def write_json(path: Path, value: Any, *, indent: int = 2) -> None:
    payload = (json.dumps(value, ensure_ascii=False, indent=indent, sort_keys=True) + "\n").encode("utf-8")
    write_bytes_atomic(path, payload)

from __future__ import annotations

import io
import re
import time
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

from .config import INTERVAL_MS
from .utils import frame_hash, iso_utc, sha256_bytes, sha256_file, write_bytes_atomic

BASE_URL = "https://data.binance.vision/data/futures/um"
EARLIEST_SCAN = date(2019, 1, 1)
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_CHECKSUM_BYTES = 4096
MAX_CSV_BYTES = 512 * 1024 * 1024
MAX_CSV_ROWS = 1_000_000
MAX_ZIP_ENTRIES = 16
MAX_COMPRESSION_RATIO = 200
SHA256_PATTERN = re.compile(r"[0-9a-fA-F]{64}")
CSV_COLUMNS = (
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "trades",
    "taker_buy_base",
    "taker_buy_quote",
    "ignore",
)


@dataclass(frozen=True)
class ArchiveRecord:
    name: str
    url: str
    path: str
    sha256: str
    bytes: int


def _month_start(value: date) -> date:
    return date(value.year, value.month, 1)


def _next_month(value: date) -> date:
    return date(value.year + (value.month == 12), 1 if value.month == 12 else value.month + 1, 1)


def _month_end(value: date) -> date:
    return _next_month(_month_start(value)) - timedelta(days=1)


def iter_months(start: date, end: date):
    cursor = _month_start(start)
    while cursor <= end:
        yield cursor
        cursor = _next_month(cursor)


def latest_complete_day(now: datetime | None = None) -> date:
    current = now or datetime.now(timezone.utc)
    return current.date() - timedelta(days=1)


class BinanceArchiveCache:
    def __init__(self, root: Path, retries: int = 4):
        if not 1 <= retries <= 10:
            raise ValueError("retries must be between 1 and 10")
        self.root = Path(root)
        self.retries = retries
        self.records: list[ArchiveRecord] = []

    def _request(self, url: str, max_bytes: int) -> bytes | None:
        for attempt in range(self.retries):
            try:
                request = Request(url, headers={"User-Agent": "GalkaLab/0.1 public-research"})
                with urlopen(request, timeout=90) as response:
                    content_length = response.headers.get("Content-Length")
                    if content_length is not None:
                        try:
                            declared = int(content_length)
                        except ValueError as error:
                            raise ValueError(f"invalid Content-Length for {url}") from error
                        if declared < 0 or declared > max_bytes:
                            raise ValueError(f"download exceeds {max_bytes} byte limit for {url}")
                    payload = bytearray()
                    while True:
                        chunk = response.read(min(1024 * 1024, max_bytes + 1 - len(payload)))
                        if not chunk:
                            break
                        payload.extend(chunk)
                        if len(payload) > max_bytes:
                            raise ValueError(f"download exceeds {max_bytes} byte limit for {url}")
                    return bytes(payload)
            except HTTPError as error:
                if error.code == 404:
                    return None
                if attempt == self.retries - 1:
                    raise
            except (TimeoutError, URLError):
                if attempt == self.retries - 1:
                    raise
            time.sleep(2**attempt)
        return None

    @staticmethod
    def _parse_checksum(payload: bytes, filename: str, *, sidecar: bool = False) -> str | None:
        try:
            parts = payload.decode("ascii").strip().split()
        except UnicodeDecodeError as error:
            raise ValueError(f"invalid checksum encoding for {filename}") from error
        if sidecar and len(parts) == 1:
            # Legacy Galka caches stored an unverifiable self-computed digest. Refresh it from
            # Binance instead of trusting it as an official checksum.
            return None
        if len(parts) != 2 or not SHA256_PATTERN.fullmatch(parts[0]):
            raise ValueError(f"invalid checksum record for {filename}")
        recorded_name = parts[1].lstrip("*")
        if recorded_name != filename:
            raise ValueError(f"checksum filename mismatch for {filename}")
        return parts[0].lower()

    def _local_path(self, cadence: str, symbol: str, interval: str, filename: str) -> Path:
        return self.root / "raw" / cadence / "klines" / symbol / interval / filename

    def fetch(self, cadence: str, symbol: str, interval: str, stamp: str) -> Path | None:
        filename = f"{symbol}-{interval}-{stamp}.zip"
        relative = f"{cadence}/klines/{symbol}/{interval}/{filename}"
        url = f"{BASE_URL}/{relative}"
        path = self._local_path(cadence, symbol, interval, filename)
        checksum_path = path.with_suffix(path.suffix + ".sha256")

        expected = None
        if checksum_path.is_symlink() or path.is_symlink():
            raise ValueError(f"symlink is not allowed in archive cache for {filename}")
        if checksum_path.exists():
            if not checksum_path.is_file() or checksum_path.stat().st_size > MAX_CHECKSUM_BYTES:
                raise ValueError(f"invalid checksum sidecar for {filename}")
            expected = self._parse_checksum(checksum_path.read_bytes(), filename, sidecar=True)
        if expected is None:
            checksum = self._request(url + ".CHECKSUM", MAX_CHECKSUM_BYTES)
            if checksum is None:
                return None
            expected = self._parse_checksum(checksum, filename)
            checksum_path.parent.mkdir(parents=True, exist_ok=True)
            write_bytes_atomic(checksum_path, f"{expected}  {filename}\n".encode("ascii"))

        if path.exists() and not path.is_file():
            raise ValueError(f"archive cache entry is not a regular file for {filename}")
        if path.exists() and path.stat().st_size <= MAX_ARCHIVE_BYTES and sha256_file(path) == expected:
            self.records.append(ArchiveRecord(filename, url, str(path), expected, path.stat().st_size))
            return path

        blob = self._request(url, MAX_ARCHIVE_BYTES)
        if blob is None:
            raise RuntimeError(f"archive disappeared after checksum was published for {filename}")
        digest = sha256_bytes(blob)
        if digest != expected:
            raise ValueError(f"checksum mismatch for {filename}")
        path.parent.mkdir(parents=True, exist_ok=True)
        write_bytes_atomic(path, blob)
        self.records.append(ArchiveRecord(filename, url, str(path), digest, len(blob)))
        return path

    def archives(self, symbol: str, interval: str, start: date, end: date) -> list[Path]:
        paths: list[Path] = []
        current_month = _month_start(datetime.now(timezone.utc).date())
        for month in iter_months(start, end):
            month_first = max(start, month)
            month_last = min(end, _month_end(month))
            monthly = None
            if month < current_month:
                monthly = self.fetch("monthly", symbol, interval, month.strftime("%Y-%m"))
            if monthly is not None:
                paths.append(monthly)
                continue
            # A missing old monthly archive means that contract/interval was not available yet.
            # Daily fallback is useful only for the latest completed month while Binance may still
            # be publishing its monthly bundle; probing every pre-listing day would be wasteful.
            previous_month = date(
                current_month.year - (current_month.month == 1),
                12 if current_month.month == 1 else current_month.month - 1,
                1,
            )
            if month < previous_month:
                continue
            cursor = month_first
            while cursor <= month_last:
                daily = self.fetch("daily", symbol, interval, cursor.isoformat())
                if daily is not None:
                    paths.append(daily)
                cursor += timedelta(days=1)
        return paths


def parse_archive(path: Path) -> pd.DataFrame:
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"archive must be a regular file: {path}")
    if path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError(f"archive exceeds {MAX_ARCHIVE_BYTES} byte limit: {path}")
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) > MAX_ZIP_ENTRIES:
            raise ValueError(f"too many entries in {path}")
        csv_entries = [entry for entry in entries if not entry.is_dir() and entry.filename.lower().endswith(".csv")]
        if len(csv_entries) != 1:
            raise ValueError(f"expected exactly one CSV in {path}")
        entry = csv_entries[0]
        member = PurePosixPath(entry.filename)
        if member.is_absolute() or ".." in member.parts or len(member.parts) != 1:
            raise ValueError(f"unsafe CSV path in {path}")
        if entry.flag_bits & 0x1:
            raise ValueError(f"encrypted CSV is not supported in {path}")
        if entry.file_size > MAX_CSV_BYTES:
            raise ValueError(f"CSV exceeds {MAX_CSV_BYTES} byte limit in {path}")
        ratio = entry.file_size / max(entry.compress_size, 1)
        if ratio > MAX_COMPRESSION_RATIO:
            raise ValueError(f"suspicious ZIP compression ratio in {path}")
        with archive.open(entry) as stream:
            raw = stream.read(MAX_CSV_BYTES + 1)
        if len(raw) > MAX_CSV_BYTES or len(raw) != entry.file_size:
            raise ValueError(f"invalid CSV size in {path}")
    frame = pd.read_csv(
        io.BytesIO(raw),
        header=None,
        names=CSV_COLUMNS,
        low_memory=False,
        nrows=MAX_CSV_ROWS + 1,
        on_bad_lines="error",
    )
    if len(frame) > MAX_CSV_ROWS:
        raise ValueError(f"CSV exceeds {MAX_CSV_ROWS} row limit in {path}")
    header_candidate = (
        tuple(str(frame.iloc[0][column]).strip().lower() for column in CSV_COLUMNS)
        if not frame.empty
        else ()
    )
    for column in ("open_time", "open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    required = ("open_time", "open", "high", "low", "close", "volume")
    invalid = frame[list(required)].isna().any(axis=1)
    if invalid.any():
        first_is_header = bool(invalid.iloc[0]) and header_candidate == CSV_COLUMNS
        if first_is_header and int(invalid.sum()) == 1:
            frame = frame.iloc[1:].reset_index(drop=True)
        else:
            raise ValueError(f"invalid numeric OHLCV row in {path}")
    if frame.empty:
        raise ValueError(f"empty CSV in {path}")
    divisor = 1_000_000 if frame["open_time"].median() > 1e14 else 1_000
    frame["time"] = pd.to_datetime(frame["open_time"] / divisor, unit="s", utc=True)
    return frame[["time", "open", "high", "low", "close", "volume"]]


def validate_market_data(frame: pd.DataFrame, interval: str) -> dict:
    if interval not in INTERVAL_MS:
        raise ValueError(f"unsupported interval {interval}")
    if frame.empty:
        raise ValueError(f"empty {interval} frame")
    times = pd.to_datetime(frame["time"], utc=True).astype("int64").to_numpy() // 1_000_000
    differences = np.diff(times)
    expected = INTERVAL_MS[interval]
    duplicates = int(pd.Series(times).duplicated().sum())
    backwards = int((differences < 0).sum())
    gap_mask = differences > expected
    gap_indices = np.flatnonzero(gap_mask)
    missing = int(np.maximum(differences[gap_mask] // expected - 1, 0).sum()) if gap_mask.any() else 0
    invalid_ohlc = int(
        (
            (frame["high"] < frame[["open", "close"]].max(axis=1))
            | (frame["low"] > frame[["open", "close"]].min(axis=1))
            | (frame["high"] < frame["low"])
            | (frame[["open", "high", "low", "close"]] <= 0).any(axis=1)
            | ~np.isfinite(frame[["open", "high", "low", "close", "volume"]]).all(axis=1)
            | (frame["volume"] < 0)
        ).sum()
    )
    return {
        "row_count": int(len(frame)),
        "duplicates": duplicates,
        "backwards": backwards,
        "gaps": int(gap_mask.sum()),
        "missing_bars": missing,
        "gap_ranges": [
            {
                "after": iso_utc(frame["time"].iloc[int(index)]),
                "before": iso_utc(frame["time"].iloc[int(index) + 1]),
                "missing_bars": int(max(differences[int(index)] // expected - 1, 0)),
            }
            for index in gap_indices
        ],
        "invalid_ohlc": invalid_ohlc,
        "start": iso_utc(frame["time"].iloc[0]),
        "end": iso_utc(frame["time"].iloc[-1]),
        "hash": frame_hash(frame),
    }


def load_market_data(
    cache: BinanceArchiveCache,
    symbol: str,
    interval: str,
    start: date | str = "auto",
    end: date | str = "latest",
) -> tuple[pd.DataFrame, dict]:
    start_date = EARLIEST_SCAN if start == "auto" else (date.fromisoformat(start) if isinstance(start, str) else start)
    end_date = latest_complete_day() if end == "latest" else (date.fromisoformat(end) if isinstance(end, str) else end)
    if start_date > end_date:
        raise ValueError("start date must not be after end date")
    paths = cache.archives(symbol, interval, start_date, end_date)
    if not paths:
        raise RuntimeError(f"no Binance archives for {symbol} {interval}")
    parts = [parse_archive(path) for path in paths]
    raw = pd.concat(parts, ignore_index=True)
    duplicate_count = int(raw["time"].duplicated().sum())
    source_time = pd.to_datetime(raw["time"], utc=True).astype("int64").to_numpy()
    source_backwards = int((np.diff(source_time) < 0).sum())
    frame = raw.sort_values("time").drop_duplicates("time", keep="last")
    dates = frame["time"].dt.date
    frame = frame[(dates >= start_date) & (dates <= end_date)].reset_index(drop=True)
    validation = validate_market_data(frame, interval)
    if validation["invalid_ohlc"]:
        raise ValueError(f"invalid OHLCV data for {symbol} {interval}")
    if source_backwards:
        raise ValueError(f"non-monotonic source archives for {symbol} {interval}")
    validation["duplicates_removed"] = duplicate_count
    validation["source_backwards"] = source_backwards
    validation.update(
        {
            "symbol": symbol,
            "interval": interval,
            "source": "Binance USD-M Futures public archive",
            "archive_count": len(paths),
            "archives": [
                record.__dict__
                for record in cache.records
                if record.name.startswith(f"{symbol}-{interval}-")
            ],
        }
    )
    return frame, validation

from __future__ import annotations

import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

import pandas as pd

from galka_lab.cli import (
    canonical_manifest_datasets,
    finalize_manifest,
    load_cached_dataset,
)
from galka_lab.config import EXECUTION_INTERVAL
from galka_lab.data import BinanceArchiveCache, CSV_COLUMNS, parse_archive, validate_market_data
from galka_lab.utils import canonical_json, frame_hash, sha256_bytes, sha256_file, write_gzip_csv


class DataTests(unittest.TestCase):
    @staticmethod
    def _archive(entries: dict[str, str]) -> bytes:
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, payload in entries.items():
                archive.writestr(name, payload)
        return output.getvalue()

    @staticmethod
    def _ohlcv_csv(*, header: bool = False) -> str:
        rows = []
        if header:
            rows.append(",".join(CSV_COLUMNS))
        rows.append("1767225600000,100,102,99,101,10,1767225659999,0,1,0,0,0")
        return "\n".join(rows) + "\n"

    def test_validation_and_hash_are_deterministic(self):
        frame = pd.DataFrame(
            {
                "time": pd.to_datetime(["2026-01-01T00:00Z", "2026-01-01T00:01Z", "2026-01-01T00:03Z"]),
                "open": [100, 101, 102],
                "high": [102, 103, 104],
                "low": [99, 100, 101],
                "close": [101, 102, 103],
                "volume": [1, 2, 3],
            }
        )
        first = validate_market_data(frame, "1m")
        second = validate_market_data(frame.copy(), "1m")
        self.assertEqual(first["gaps"], 1)
        self.assertEqual(first["missing_bars"], 1)
        self.assertEqual(first["gap_ranges"][0]["after"], "2026-01-01T00:01:00Z")
        self.assertEqual(first["gap_ranges"][0]["before"], "2026-01-01T00:03:00Z")
        self.assertEqual(first["invalid_ohlc"], 0)
        self.assertEqual(first["hash"], second["hash"])
        self.assertEqual(first["hash"], frame_hash(frame))

    def test_manifest_hash_ignores_parallel_completion_order(self):
        datasets = [
            {
                "symbol": symbol,
                "interval": interval,
                "start": "2026-01-01",
                "end": "2026-01-02",
                "hash": f"{symbol}-{interval}",
            }
            for interval in ("5m", "15m", EXECUTION_INTERVAL)
            for symbol in ("BTCUSDT", "ETHUSDT")
        ]
        symbols = ("BTCUSDT", "ETHUSDT")
        intervals = ("5m", "15m")
        first = canonical_manifest_datasets(datasets, symbols, intervals)
        second = canonical_manifest_datasets(list(reversed(datasets)), symbols, intervals)
        self.assertEqual(first, second)
        self.assertEqual(
            sha256_bytes(canonical_json({"datasets": first}).encode("utf-8")),
            sha256_bytes(canonical_json({"datasets": second}).encode("utf-8")),
        )
        self.assertEqual(
            [(item["interval"], item["symbol"]) for item in first],
            [
                ("5m", "BTCUSDT"),
                ("5m", "ETHUSDT"),
                ("15m", "BTCUSDT"),
                ("15m", "ETHUSDT"),
                (EXECUTION_INTERVAL, "BTCUSDT"),
                (EXECUTION_INTERVAL, "ETHUSDT"),
            ],
        )

    def test_gzip_csv_is_byte_reproducible(self):
        frame = pd.DataFrame({"value": [1, 2], "label": ["a", "b"]})
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first.csv.gz"
            second = Path(directory) / "second.csv.gz"
            write_gzip_csv(first, frame)
            write_gzip_csv(second, frame)
            self.assertEqual(sha256_file(first), sha256_file(second))
            self.assertEqual(
                pd.read_csv(first).to_dict(orient="records"),
                frame.to_dict(orient="records"),
            )

    def test_archive_requires_official_checksum_and_reuses_verified_cache(self):
        filename = "BTCUSDT-1m-2026-01.zip"
        archive_blob = self._archive({filename.replace(".zip", ".csv"): self._ohlcv_csv()})
        digest = sha256_bytes(archive_blob)

        class FakeCache(BinanceArchiveCache):
            def __init__(self, root: Path, responses: dict[str, bytes | None]):
                super().__init__(root, retries=1)
                self.responses = responses
                self.calls: list[str] = []

            def _request(self, url: str, max_bytes: int) -> bytes | None:
                self.calls.append(url)
                result = self.responses.get(url)
                if result is not None and len(result) > max_bytes:
                    raise ValueError("test response exceeds limit")
                return result

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = "https://data.binance.vision/data/futures/um/monthly/klines/BTCUSDT/1m/" + filename
            cache = FakeCache(
                root,
                {
                    base + ".CHECKSUM": f"{digest}  {filename}\n".encode("ascii"),
                    base: archive_blob,
                },
            )
            path = cache.fetch("monthly", "BTCUSDT", "1m", "2026-01")
            self.assertIsNotNone(path)
            self.assertEqual(sha256_file(path), digest)
            self.assertEqual(len(parse_archive(path)), 1)

            cached = FakeCache(root, {})
            self.assertEqual(cached.fetch("monthly", "BTCUSDT", "1m", "2026-01"), path)
            self.assertEqual(cached.calls, [])

            missing = FakeCache(root / "missing", {})
            self.assertIsNone(missing.fetch("monthly", "BTCUSDT", "1m", "2025-12"))
            self.assertEqual(len(missing.calls), 1)
            self.assertTrue(missing.calls[0].endswith(".CHECKSUM"))

    def test_archive_parser_rejects_unsafe_or_ambiguous_members(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            unsafe = root / "unsafe.zip"
            unsafe.write_bytes(self._archive({"../prices.csv": self._ohlcv_csv()}))
            with self.assertRaisesRegex(ValueError, "unsafe CSV path"):
                parse_archive(unsafe)

            ambiguous = root / "ambiguous.zip"
            ambiguous.write_bytes(
                self._archive({"first.csv": self._ohlcv_csv(), "second.csv": self._ohlcv_csv()})
            )
            with self.assertRaisesRegex(ValueError, "exactly one CSV"):
                parse_archive(ambiguous)

            malformed = root / "malformed.zip"
            malformed.write_bytes(
                self._archive({"prices.csv": "bad,100,102,99,101,10,0,0,0,0,0,0\n"})
            )
            with self.assertRaisesRegex(ValueError, "invalid numeric OHLCV"):
                parse_archive(malformed)

            with_header = root / "header.zip"
            with_header.write_bytes(self._archive({"prices.csv": self._ohlcv_csv(header=True)}))
            self.assertEqual(len(parse_archive(with_header)), 1)

    def test_cached_dataset_is_bound_to_manifest(self):
        timestamps = {
            column: ["2026-01-01T00:00:00Z"]
            for column in (
                "pivot_time",
                "confirmation_time",
                "feature_cutoff_time",
                "activation_time",
                "return_time",
                "reclaim_time",
                "outcome_end_time",
            )
        }
        frame = pd.DataFrame({**timestamps, "analysis_eligible": [True]})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "work" / "dataset.csv.gz"
            manifest_path = root / "data_manifest.json"
            write_gzip_csv(dataset, frame)
            manifest = finalize_manifest({"schema_version": "test"}, dataset, len(frame))
            manifest_path.write_text(json.dumps(manifest))
            loaded, verified = load_cached_dataset(dataset, manifest_path)
            self.assertEqual(len(loaded), 1)
            self.assertEqual(verified["manifest_hash"], manifest["manifest_hash"])

            dataset.write_bytes(dataset.read_bytes() + b"tampered")
            with self.assertRaisesRegex(ValueError, "size mismatch"):
                load_cached_dataset(dataset, manifest_path)

            write_gzip_csv(dataset, frame)
            changed = dict(manifest)
            changed["schema_version"] = "tampered"
            manifest_path.write_text(json.dumps(changed))
            with self.assertRaisesRegex(ValueError, "manifest hash mismatch"):
                load_cached_dataset(dataset, manifest_path)


if __name__ == "__main__":
    unittest.main()

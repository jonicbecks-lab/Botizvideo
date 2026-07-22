from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .clustering import apply_types, fit_types
from .config import (
    DATASET_SCHEMA_VERSION,
    EXECUTION_INTERVAL,
    MODEL_VERSION,
    PRIMARY_INTERVALS,
    ROBUSTNESS_INTERVALS,
    SEED,
    SYMBOLS,
)
from .data import BinanceArchiveCache, load_market_data
from .detector import extract_candidates
from .evaluation import derive_grid_profiles, evaluate_profiles
from .features import build_market_features, enrich_cross_asset
from .outcomes import label_outcomes
from .pack import build_terminal_pack, write_terminal_pack
from .report import write_reports
from .splits import assert_oos_isolation, assign_chronological_splits, mark_split_embargo
from .statistics import build_statistics
from .utils import canonical_json, frame_hash, sha256_bytes, sha256_file, write_gzip_csv, write_json
from .validation import walk_forward_validate


MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_CACHED_DATASET_BYTES = 8 * 1024 * 1024 * 1024
MAX_CACHED_DATASET_ROWS = 5_000_000


def _rule(interval: str) -> str:
    return {"1m": "1min", "5m": "5min", "15m": "15min", "30m": "30min", "1h": "1h"}[interval]


def resample_ohlcv(frame: pd.DataFrame, interval: str) -> pd.DataFrame:
    indexed = frame.set_index("time")
    result = indexed.resample(_rule(interval), label="left", closed="left", origin="epoch").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    )
    return result.dropna().reset_index()


def synthetic_execution(symbol: str, days: int = 120) -> pd.DataFrame:
    seed = SEED + SYMBOLS.index(symbol) * 97
    rng = np.random.default_rng(seed)
    count = days * 24 * 60
    returns = rng.normal(0, 0.00032, count)
    for index in range(600, count - 20, 780):
        magnitude = rng.uniform(0.006, 0.018)
        returns[index : index + 4] += np.array([-magnitude, -magnitude * 0.55, magnitude * 0.65, magnitude * 0.45])
    base = {"BTCUSDT": 60_000, "ETHUSDT": 3_000, "SOLUSDT": 150}[symbol]
    close = base * np.exp(np.cumsum(returns))
    open_ = np.r_[close[0], close[:-1]]
    spread = rng.uniform(0.0001, 0.0012, count)
    high = np.maximum(open_, close) * (1 + spread)
    low = np.minimum(open_, close) * (1 - spread)
    volume = rng.lognormal(5, 0.7, count) * (1 + np.abs(returns) * 100)
    return pd.DataFrame(
        {
            "time": pd.date_range("2026-01-01", periods=count, freq="1min", tz="UTC"),
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )


def _prefetch(args, datasets: list[tuple[str, str]]) -> None:
    def task(symbol: str, interval: str):
        cache = BinanceArchiveCache(Path(args.cache))
        start = "auto" if args.start == "auto" else pd.Timestamp(args.start).date()
        end = args.end if args.end == "latest" else pd.Timestamp(args.end).date()
        from .data import EARLIEST_SCAN, latest_complete_day

        start_date = EARLIEST_SCAN if start == "auto" else start
        end_date = latest_complete_day() if end == "latest" else end
        paths = cache.archives(symbol, interval, start_date, end_date)
        return symbol, interval, len(paths)

    print(f"PREFETCH {len(datasets)} symbol/interval datasets", flush=True)
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = [pool.submit(task, symbol, interval) for symbol, interval in datasets]
        for future in as_completed(futures):
            symbol, interval, count = future.result()
            print(f"  {symbol} {interval}: {count} archives", flush=True)


def _load_one(args, symbol: str, interval: str):
    cache = BinanceArchiveCache(Path(args.cache))
    return load_market_data(cache, symbol, interval, args.start, args.end)


def canonical_manifest_datasets(
    datasets: list[dict], symbols: tuple[str, ...], intervals: tuple[str, ...]
) -> list[dict]:
    """Remove thread-completion order from the source manifest."""
    symbol_order = {symbol: index for index, symbol in enumerate(symbols)}
    interval_order = {
        interval: index
        for index, interval in enumerate(dict.fromkeys((*intervals, EXECUTION_INTERVAL)))
    }
    return sorted(
        datasets,
        key=lambda item: (
            interval_order.get(item.get("interval"), len(interval_order)),
            symbol_order.get(item.get("symbol"), len(symbol_order)),
            str(item.get("interval", "")),
            str(item.get("symbol", "")),
            str(item.get("start", "")),
            str(item.get("end", "")),
            str(item.get("hash", "")),
        ),
    )


def finalize_manifest(manifest: dict, dataset_path: Path, row_count: int) -> dict:
    """Bind the reusable analysis dataset to a self-verifying source manifest."""
    if dataset_path.is_symlink() or not dataset_path.is_file():
        raise ValueError("analysis dataset must be a regular file")
    payload = {key: value for key, value in manifest.items() if key != "manifest_hash"}
    payload["analysis_dataset"] = {
        "bytes": dataset_path.stat().st_size,
        "rows": int(row_count),
        "sha256": sha256_file(dataset_path),
    }
    payload["manifest_hash"] = sha256_bytes(canonical_json(payload).encode("utf-8"))
    return payload


def load_cached_dataset(dataset_path: Path, manifest_path: Path) -> tuple[pd.DataFrame, dict]:
    """Fail closed if a cached dataset or its provenance manifest was changed or truncated."""
    for path, limit, label in (
        (dataset_path, MAX_CACHED_DATASET_BYTES, "analysis dataset"),
        (manifest_path, MAX_MANIFEST_BYTES, "data manifest"),
    ):
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"{label} must be a regular file")
        if path.stat().st_size > limit:
            raise ValueError(f"{label} exceeds its size limit")
    try:
        manifest = json.loads(manifest_path.read_bytes())
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("invalid data manifest JSON") from error
    if not isinstance(manifest, dict):
        raise ValueError("data manifest must be an object")
    recorded_hash = manifest.get("manifest_hash")
    unsigned = {key: value for key, value in manifest.items() if key != "manifest_hash"}
    expected_hash = sha256_bytes(canonical_json(unsigned).encode("utf-8"))
    if not isinstance(recorded_hash, str) or recorded_hash != expected_hash:
        raise ValueError("data manifest hash mismatch; rebuild with --build-dataset")
    dataset = manifest.get("analysis_dataset")
    if not isinstance(dataset, dict):
        raise ValueError("manifest has no bound analysis dataset; rebuild with --build-dataset")
    expected_keys = {"bytes", "rows", "sha256"}
    if set(dataset) != expected_keys:
        raise ValueError("invalid analysis dataset manifest")
    if not isinstance(dataset["bytes"], int) or dataset["bytes"] != dataset_path.stat().st_size:
        raise ValueError("analysis dataset size mismatch; rebuild with --build-dataset")
    if not isinstance(dataset["rows"], int) or not 1 <= dataset["rows"] <= MAX_CACHED_DATASET_ROWS:
        raise ValueError("invalid analysis dataset row count")
    if not isinstance(dataset["sha256"], str) or dataset["sha256"] != sha256_file(dataset_path):
        raise ValueError("analysis dataset checksum mismatch; rebuild with --build-dataset")
    events = pd.read_csv(
        dataset_path,
        parse_dates=["pivot_time", "confirmation_time", "feature_cutoff_time", "activation_time", "return_time", "reclaim_time", "outcome_end_time"],
    )
    if len(events) != dataset["rows"]:
        raise ValueError("analysis dataset row count mismatch; rebuild with --build-dataset")
    return events, manifest


def build_dataset(args) -> tuple[pd.DataFrame, dict]:
    intervals = tuple(args.intervals)
    symbols = tuple(args.symbols)
    manifests = []
    candidates_by_interval = []
    synthetic_frames = {symbol: synthetic_execution(symbol, args.synthetic_days) for symbol in symbols} if args.synthetic else {}

    if not args.synthetic and not args.skip_prefetch:
        datasets = [(symbol, interval) for symbol in symbols for interval in (*intervals, EXECUTION_INTERVAL)]
        _prefetch(args, sorted(set(datasets)))

    for interval in intervals:
        print(f"CANDIDATES {interval}", flush=True)
        raw_frames = {}
        if args.synthetic:
            for symbol in symbols:
                raw_frames[symbol] = resample_ohlcv(synthetic_frames[symbol], interval)
                manifests.append(
                    {
                        "symbol": symbol,
                        "interval": interval,
                        "source": "deterministic synthetic fixture",
                        "row_count": len(raw_frames[symbol]),
                        "start": raw_frames[symbol]["time"].iloc[0].isoformat(),
                        "end": raw_frames[symbol]["time"].iloc[-1].isoformat(),
                        "hash": frame_hash(raw_frames[symbol]),
                        "gaps": 0,
                        "duplicates": 0,
                        "invalid_ohlc": 0,
                    }
                )
        else:
            with ThreadPoolExecutor(max_workers=min(len(symbols), max(1, args.workers))) as pool:
                future_map = {pool.submit(_load_one, args, symbol, interval): symbol for symbol in symbols}
                for future in as_completed(future_map):
                    symbol = future_map[future]
                    raw_frames[symbol], manifest = future.result()
                    manifests.append(manifest)
        contexts = {symbol: build_market_features(raw_frames[symbol], interval) for symbol in symbols}
        detected = []
        for symbol in symbols:
            candidates = extract_candidates(contexts[symbol], symbol, interval)
            print(f"  {symbol}: {len(candidates)} broad candidates", flush=True)
            detected.append(candidates)
        interval_candidates = pd.concat(detected, ignore_index=True) if detected else pd.DataFrame()
        interval_candidates = enrich_cross_asset(interval_candidates, contexts)
        candidates_by_interval.append(interval_candidates)
        del raw_frames, contexts

    candidates = pd.concat(candidates_by_interval, ignore_index=True)
    if candidates.empty:
        raise RuntimeError("candidate detector produced no rows")
    outcomes = []
    for symbol in symbols:
        print(f"OUTCOMES {symbol} on 1m execution history", flush=True)
        if args.synthetic:
            execution = synthetic_frames[symbol]
            manifests.append(
                {
                    "symbol": symbol,
                    "interval": EXECUTION_INTERVAL,
                    "source": "deterministic synthetic fixture",
                    "row_count": len(execution),
                    "start": execution["time"].iloc[0].isoformat(),
                    "end": execution["time"].iloc[-1].isoformat(),
                    "hash": frame_hash(execution),
                    "gaps": 0,
                    "duplicates": 0,
                    "invalid_ohlc": 0,
                }
            )
        else:
            execution, manifest = _load_one(args, symbol, EXECUTION_INTERVAL)
            manifests.append(manifest)
        symbol_candidates = candidates[candidates["symbol"] == symbol]
        outcomes.append(label_outcomes(symbol_candidates, execution))
        del execution
    events = pd.concat(outcomes, ignore_index=True)
    events = events.sort_values(["symbol", "interval", "confirmation_time", "candidate_id"]).reset_index(drop=True)
    events["analysis_eligible"] = False
    representative = events.groupby("event_family_id", sort=False).apply(
        lambda group: (group["drop_atr"] * group["recovery_ratio"]).idxmax(), include_groups=False
    )
    events.loc[representative.to_numpy(int), "analysis_eligible"] = True
    events = assign_chronological_splits(events)
    assert_oos_isolation(events)
    events = mark_split_embargo(events)

    manifest_payload = {
        "schema_version": DATASET_SCHEMA_VERSION,
        "source": "Binance USD-M Futures public OHLCV" if not args.synthetic else "synthetic",
        "symbols": list(symbols),
        "intervals": list(intervals),
        "execution_interval": EXECUTION_INTERVAL,
        "start": min(item["start"] for item in manifests),
        "end": max(item["end"] for item in manifests),
        "datasets": canonical_manifest_datasets(manifests, symbols, intervals),
        "candidate_count": int(len(events)),
        "family_representative_count": int(events["analysis_eligible"].sum()),
        "analysis_event_count": int(
            (events["analysis_eligible"] & ~events["purged_for_split"]).sum()
        ),
    }
    return events, manifest_payload


def run(args) -> dict:
    output = Path(args.output)
    work = output / "work"
    work.mkdir(parents=True, exist_ok=True)
    dataset_path = work / "dataset.csv.gz"
    manifest_path = output / "data_manifest.json"
    if args.build_dataset or not dataset_path.exists():
        events, manifest = build_dataset(args)
        write_gzip_csv(dataset_path, events)
        manifest = finalize_manifest(manifest, dataset_path, len(events))
        write_json(manifest_path, manifest)
    else:
        events, manifest = load_cached_dataset(dataset_path, manifest_path)

    classification = events[events["analysis_eligible"] == True].copy()  # noqa: E712
    clustered = fit_types(classification)
    all_candidates = apply_types(events, clustered.model)
    analysis = classification[classification["purged_for_split"] == False].copy()  # noqa: E712
    analysis = apply_types(analysis, clustered.model)
    profiles = derive_grid_profiles(analysis)
    evaluated, evaluation = evaluate_profiles(analysis, profiles)
    statistics = build_statistics(evaluated)
    walk_forward = walk_forward_validate(evaluated, clustered.model["selected_k"])
    evaluation["walk_forward"] = walk_forward

    generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    run_metadata = {
        "model_version": MODEL_VERSION,
        "model_hash": clustered.model["model_hash"],
        "generated_at": generated_at,
        "seed": SEED,
        "chronological_split": "60% train / 20% validation / 20% final OOS by global timestamp groups",
        "walk_forward_folds": len(walk_forward),
        "final_oos_used_for_fit": False,
        "candidate_count": int(len(all_candidates)),
        "analysis_event_count": int(len(evaluated)),
        "purged_event_count": int(events["purged_for_split"].sum()),
        "activated_event_count": int((evaluated["activated"] == True).sum()),  # noqa: E712
        "manifest_hash": manifest["manifest_hash"],
        "arguments": vars(args),
    }
    write_gzip_csv(output / "all_candidates.csv.gz", all_candidates)
    write_gzip_csv(output / "events.csv.gz", evaluated)
    dataset_directory = output / "dataset"
    dataset_directory.mkdir(parents=True, exist_ok=True)
    dataset_index = []
    for dataset_name, dataset in (("all_candidates", all_candidates), ("events", evaluated)):
        for (symbol, interval), partition in dataset.groupby(["symbol", "interval"], sort=True):
            path = dataset_directory / f"{dataset_name}_{symbol}_{interval}.csv.gz"
            write_gzip_csv(path, partition)
            dataset_index.append(
                {
                    "dataset": dataset_name,
                    "symbol": symbol,
                    "interval": interval,
                    "rows": int(len(partition)),
                    "path": str(path.relative_to(output)),
                    "bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )
    write_json(output / "dataset_index.json", dataset_index)
    write_reports(
        output,
        events=evaluated,
        statistics=statistics,
        model=clustered.model,
        evaluation=evaluation,
        manifest=manifest,
        run_metadata=run_metadata,
    )
    write_json(output / "walk_forward.json", walk_forward)
    if args.export_terminal_pack:
        pack = build_terminal_pack(
            statistics=statistics,
            model=clustered.model,
            evaluation=evaluation,
            manifest=manifest,
            generated_at=generated_at,
        )
        write_terminal_pack(output / "galka-stats-v1.json", pack)
    print(json.dumps(run_metadata, ensure_ascii=False, indent=2), flush=True)
    return run_metadata


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Build the reproducible Galka Lab statistics dataset")
    value.add_argument("--symbols", nargs="+", default=list(SYMBOLS), choices=SYMBOLS)
    value.add_argument("--intervals", nargs="+", default=list((*PRIMARY_INTERVALS, *ROBUSTNESS_INTERVALS)), choices=("5m", "15m", "30m", "1h"))
    value.add_argument("--from", dest="start", default="auto")
    value.add_argument("--to", dest="end", default="latest")
    value.add_argument("--cache", default="research/galka_lab/cache/binance")
    value.add_argument("--output", default="research/galka_lab/output")
    value.add_argument("--workers", type=int, default=8)
    value.add_argument("--build-dataset", action="store_true")
    value.add_argument("--classify", action="store_true")
    value.add_argument("--report", action="store_true")
    value.add_argument("--export-terminal-pack", action="store_true")
    value.add_argument("--synthetic", action="store_true")
    value.add_argument("--synthetic-days", type=int, default=120)
    value.add_argument("--skip-prefetch", action="store_true")
    return value


def main() -> None:
    args = parser().parse_args()
    if not any((args.build_dataset, args.classify, args.report, args.export_terminal_pack)):
        args.build_dataset = args.classify = args.report = args.export_terminal_pack = True
    run(args)

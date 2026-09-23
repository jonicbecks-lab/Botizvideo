from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import random
import statistics
from typing import Iterable, Mapping, Sequence


DEFAULT_HORIZONS = (1, 2, 4, 10, 30, 60, 120)
DEFAULT_COST_SCENARIOS_BPS = (0.0, 2.0, 5.0)


def _jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _day(start_ms: int) -> str:
    return datetime.fromtimestamp(int(start_ms) / 1000.0, tz=timezone.utc).date().isoformat()


def _price_index(window_rows: Iterable[Mapping]) -> tuple[dict[tuple[str, str, int], float], int]:
    index: dict[tuple[str, str, int], float] = {}
    inferred_bucket_ms = 30_000
    for row in window_rows:
        asset = str(row.get("asset", ""))
        start = int(row.get("start_ms", 0))
        end = int(row.get("end_ms", start + inferred_bucket_ms))
        if end > start:
            inferred_bucket_ms = end - start
        observations = row.get("response_observations") or {}
        for market in ("spot", "perp"):
            obs = observations.get(market) or {}
            close = obs.get("reference_close")
            if close is not None:
                index[(asset, market, start)] = float(close)
    return index, inferred_bucket_ms


def attach_catalog_forward_labels(
    events: Iterable[Mapping],
    window_rows: Iterable[Mapping],
    horizons_buckets: Sequence[int] = DEFAULT_HORIZONS,
) -> list[dict]:
    """Attach forward labels without changing any trigger-time feature.

    Outcome price uses the same equal-venue median close saved by synchronized replay.
    MFE/MAE are computed on the sequence of completed 30-second closes, not intrabar
    highs/lows, and are explicitly named close-path metrics.
    """
    windows = list(window_rows)
    price, bucket_ms = _price_index(windows)
    out: list[dict] = []
    for raw in events:
        event = dict(raw)
        asset = str(event.get("asset", ""))
        market = str(event.get("market") or "perp")
        start = int(event["start_ms"])
        current = price.get((asset, market, start))
        event["outcome_market"] = market
        event["outcome_price_basis"] = "median_active_venue_close_on_quality_passed_windows"
        if current is None or current == 0:
            for h in horizons_buckets:
                for suffix in (
                    "raw_bps", "hypothesis_bps", "close_path_mfe_bps", "close_path_mae_bps"
                ):
                    event[f"future_{int(h)}b_{suffix}"] = None
            out.append(event)
            continue

        direction = int(event.get("hypothesis_direction", 0) or 0)
        for h in horizons_buckets:
            h = int(h)
            future = price.get((asset, market, start + h * bucket_ms))
            raw_bps = ((future / current) - 1.0) * 10000.0 if future is not None else None
            event[f"future_{h}b_raw_bps"] = raw_bps
            event[f"future_{h}b_hypothesis_bps"] = (
                raw_bps * direction if raw_bps is not None and direction else None
            )

            path = [
                price.get((asset, market, start + k * bucket_ms))
                for k in range(1, h + 1)
            ]
            if direction == 0 or any(v is None for v in path):
                event[f"future_{h}b_close_path_mfe_bps"] = None
                event[f"future_{h}b_close_path_mae_bps"] = None
                continue
            path_bps = [((float(v) / current) - 1.0) * 10000.0 for v in path]
            if direction > 0:
                mfe = max(0.0, max(path_bps))
                mae = max(0.0, -min(path_bps))
            else:
                mfe = max(0.0, -min(path_bps))
                mae = max(0.0, max(path_bps))
            event[f"future_{h}b_close_path_mfe_bps"] = mfe
            event[f"future_{h}b_close_path_mae_bps"] = mae
        out.append(event)
    return out


def _block_values(rows: Sequence[Mapping], key: str) -> dict[str, list[float]]:
    blocks: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        value = row.get(key)
        if value is not None:
            blocks[_day(int(row["start_ms"]))].append(float(value))
    return dict(blocks)


def day_block_bootstrap_mean_ci(
    blocks: Mapping[str, Sequence[float]],
    *,
    draws: int = 2000,
    seed: int = 1729,
) -> tuple[float | None, float | None]:
    days = sorted(k for k, vals in blocks.items() if vals)
    if len(days) < 2 or draws < 1:
        return None, None
    rng = random.Random(seed)
    means: list[float] = []
    for _ in range(draws):
        sampled = [rng.choice(days) for _ in days]
        vals = [v for d in sampled for v in blocks[d]]
        if vals:
            means.append(statistics.fmean(vals))
    if not means:
        return None, None
    means.sort()
    lo_i = max(0, int(math.floor(0.025 * (len(means) - 1))))
    hi_i = min(len(means) - 1, int(math.ceil(0.975 * (len(means) - 1))))
    return means[lo_i], means[hi_i]


def day_block_signflip_p(
    blocks: Mapping[str, Sequence[float]],
    *,
    draws: int = 5000,
    seed: int = 1729,
) -> float | None:
    """Two-sided day-block random-sign test for a zero-mean null."""
    days = sorted(k for k, vals in blocks.items() if vals)
    if len(days) < 2:
        return None
    day_sums = [sum(float(v) for v in blocks[d]) for d in days]
    day_ns = [len(blocks[d]) for d in days]
    total_n = sum(day_ns)
    observed = abs(sum(day_sums) / total_n)

    if len(days) <= 12:
        total = 1 << len(days)
        extreme = 0
        for mask in range(total):
            signed_sum = sum(
                s if (mask >> i) & 1 else -s for i, s in enumerate(day_sums)
            )
            if abs(signed_sum / total_n) >= observed - 1e-15:
                extreme += 1
        return extreme / total

    rng = random.Random(seed)
    extreme = 0
    draws = max(1, draws)
    for _ in range(draws):
        signed_sum = sum(s if rng.random() < 0.5 else -s for s in day_sums)
        if abs(signed_sum / total_n) >= observed - 1e-15:
            extreme += 1
    return (extreme + 1) / (draws + 1)


def _bh_qvalues(pairs: Sequence[tuple[tuple[int, str], float]]) -> dict[tuple[int, str], float]:
    """Benjamini-Hochberg q-values for arbitrary test identifiers."""
    valid = sorted(((key, float(p)) for key, p in pairs if p is not None), key=lambda x: x[1])
    m = len(valid)
    if not m:
        return {}
    raw_q = [min(1.0, p * m / (i + 1)) for i, (_, p) in enumerate(valid)]
    for i in range(m - 2, -1, -1):
        raw_q[i] = min(raw_q[i], raw_q[i + 1])
    return {valid[i][0]: raw_q[i] for i in range(m)}


def summarize_catalog(
    labeled_events: Iterable[Mapping],
    *,
    horizons_buckets: Sequence[int] = DEFAULT_HORIZONS,
    cost_scenarios_bps: Sequence[float] = DEFAULT_COST_SCENARIOS_BPS,
    bootstrap_draws: int = 2000,
    signflip_draws: int = 5000,
    seed: int = 1729,
) -> list[dict]:
    all_rows = [dict(r) for r in labeled_events]
    groups: dict[tuple[str, str, str, str], list[dict]] = defaultdict(list)
    for row in all_rows:
        family = str(row.get("pattern_family", "unknown"))
        label = str(row.get("pattern_label", family))
        groups[(str(row.get("asset", "")), str(row.get("market") or "perp"), family, label)].append(row)

    out: list[dict] = []
    raw_tests: list[tuple[tuple[int, str], float]] = []
    hyp_tests: list[tuple[tuple[int, str], float]] = []

    for group_index, ((asset, market, family, label), raw_group) in enumerate(sorted(groups.items())):
        rows = [r for r in raw_group if bool(r.get("is_independent_event", True))]
        result: dict = {
            "asset": asset,
            "market": market,
            "pattern_family": family,
            "pattern_label": label,
            "raw_event_count": len(raw_group),
            "independent_event_count": len(rows),
            "independent_day_count": len({_day(int(r["start_ms"])) for r in rows}),
        }
        for h in horizons_buckets:
            h = int(h)
            prefix = f"h{h}"
            raw_key = f"future_{h}b_raw_bps"
            hyp_key = f"future_{h}b_hypothesis_bps"
            mfe_key = f"future_{h}b_close_path_mfe_bps"
            mae_key = f"future_{h}b_close_path_mae_bps"
            raw_vals = [float(r[raw_key]) for r in rows if r.get(raw_key) is not None]
            hyp_vals = [float(r[hyp_key]) for r in rows if r.get(hyp_key) is not None]
            mfe_vals = [float(r[mfe_key]) for r in rows if r.get(mfe_key) is not None]
            mae_vals = [float(r[mae_key]) for r in rows if r.get(mae_key) is not None]

            result[f"{prefix}_n_raw"] = len(raw_vals)
            result[f"{prefix}_mean_raw_bps"] = statistics.fmean(raw_vals) if raw_vals else None
            result[f"{prefix}_median_raw_bps"] = statistics.median(raw_vals) if raw_vals else None
            result[f"{prefix}_raw_positive_rate"] = (
                sum(v > 0 for v in raw_vals) / len(raw_vals) if raw_vals else None
            )
            raw_blocks = _block_values(rows, raw_key)
            raw_lo, raw_hi = day_block_bootstrap_mean_ci(
                raw_blocks, draws=bootstrap_draws, seed=seed + group_index * 1000 + h
            )
            raw_p = day_block_signflip_p(
                raw_blocks, draws=signflip_draws, seed=seed + group_index * 1000 + h
            )
            result[f"{prefix}_raw_mean_ci95_low_bps"] = raw_lo
            result[f"{prefix}_raw_mean_ci95_high_bps"] = raw_hi
            result[f"{prefix}_raw_day_block_p"] = raw_p
            if raw_p is not None:
                raw_tests.append(((group_index, prefix), raw_p))

            result[f"{prefix}_n_hypothesis"] = len(hyp_vals)
            result[f"{prefix}_mean_hypothesis_bps"] = statistics.fmean(hyp_vals) if hyp_vals else None
            result[f"{prefix}_median_hypothesis_bps"] = statistics.median(hyp_vals) if hyp_vals else None
            result[f"{prefix}_hypothesis_hit_rate"] = (
                sum(v > 0 for v in hyp_vals) / len(hyp_vals) if hyp_vals else None
            )
            hyp_blocks = _block_values(rows, hyp_key)
            hyp_lo, hyp_hi = day_block_bootstrap_mean_ci(
                hyp_blocks, draws=bootstrap_draws, seed=seed + 500_000 + group_index * 1000 + h
            )
            hyp_p = day_block_signflip_p(
                hyp_blocks, draws=signflip_draws, seed=seed + 500_000 + group_index * 1000 + h
            )
            result[f"{prefix}_hypothesis_mean_ci95_low_bps"] = hyp_lo
            result[f"{prefix}_hypothesis_mean_ci95_high_bps"] = hyp_hi
            result[f"{prefix}_hypothesis_day_block_p"] = hyp_p
            if hyp_p is not None:
                hyp_tests.append(((group_index, prefix), hyp_p))

            result[f"{prefix}_mean_close_path_mfe_bps"] = statistics.fmean(mfe_vals) if mfe_vals else None
            result[f"{prefix}_median_close_path_mfe_bps"] = statistics.median(mfe_vals) if mfe_vals else None
            result[f"{prefix}_mean_close_path_mae_bps"] = statistics.fmean(mae_vals) if mae_vals else None
            result[f"{prefix}_median_close_path_mae_bps"] = statistics.median(mae_vals) if mae_vals else None

            for cost in cost_scenarios_bps:
                key_cost = str(float(cost)).replace(".", "p")
                if hyp_vals:
                    net = [v - float(cost) for v in hyp_vals]
                    result[f"{prefix}_cost_{key_cost}_mean_net_bps"] = statistics.fmean(net)
                    result[f"{prefix}_cost_{key_cost}_net_hit_rate"] = sum(v > 0 for v in net) / len(net)
                else:
                    result[f"{prefix}_cost_{key_cost}_mean_net_bps"] = None
                    result[f"{prefix}_cost_{key_cost}_net_hit_rate"] = None
        out.append(result)

    raw_q = _bh_qvalues(raw_tests)
    hyp_q = _bh_qvalues(hyp_tests)
    for group_index, result in enumerate(out):
        for h in horizons_buckets:
            prefix = f"h{int(h)}"
            result[f"{prefix}_raw_fdr_q"] = raw_q.get((group_index, prefix))
            result[f"{prefix}_hypothesis_fdr_q"] = hyp_q.get((group_index, prefix))
    return out


def run_catalog_study(
    *,
    events_path: Path,
    windows_path: Path,
    output_dir: Path,
    horizons_buckets: Sequence[int] = DEFAULT_HORIZONS,
    bootstrap_draws: int = 2000,
    signflip_draws: int = 5000,
) -> dict:
    events = _jsonl(events_path)
    windows = _jsonl(windows_path)
    labeled = attach_catalog_forward_labels(events, windows, horizons_buckets)
    summary_rows = summarize_catalog(
        labeled,
        horizons_buckets=horizons_buckets,
        bootstrap_draws=bootstrap_draws,
        signflip_draws=signflip_draws,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "labeled_pattern_events.jsonl").open("w", encoding="utf-8") as f:
        for row in labeled:
            f.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")
    payload = {
        "study": "FLOW_LAB_PATTERN_CATALOG_EVENT_STUDY",
        "event_source": str(events_path),
        "window_source": str(windows_path),
        "horizons_buckets": [int(h) for h in horizons_buckets],
        "cost_scenarios_bps": list(DEFAULT_COST_SCENARIOS_BPS),
        "bootstrap": {
            "kind": "UTC-day block bootstrap of mean",
            "draws": bootstrap_draws,
            "ci": 0.95,
        },
        "multiple_testing": {
            "test": "two-sided UTC-day block sign-flip",
            "draws_when_not_exact": signflip_draws,
            "correction": "Benjamini-Hochberg FDR across all catalog groups and horizons; raw and preregistered-direction tests corrected separately",
        },
        "mae_mfe": "close-path only on completed quality-passed windows; not intrabar extremes",
        "groups": summary_rows,
    }
    (output_dir / "catalog_study.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
    )
    return payload


def main() -> None:
    p = argparse.ArgumentParser(description="Event study for synchronized Flow Lab pattern catalog")
    p.add_argument("--events", default="results/flow_lab/synchronized-patterns/pattern_events.jsonl")
    p.add_argument("--windows", default="results/flow_lab/synchronized-patterns/pattern_windows.jsonl")
    p.add_argument("--output-dir", default="results/flow_lab/catalog-study")
    p.add_argument("--bootstrap-draws", type=int, default=2000)
    p.add_argument("--signflip-draws", type=int, default=5000)
    args = p.parse_args()
    payload = run_catalog_study(
        events_path=Path(args.events),
        windows_path=Path(args.windows),
        output_dir=Path(args.output_dir),
        bootstrap_draws=args.bootstrap_draws,
        signflip_draws=args.signflip_draws,
    )
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from .catalog_study import run_catalog_study
from .collector import collect
from .micro_collector import run as collect_micro
from .window_replay import build_pattern_dataset


async def collect_for(duration_sec: float, root: Path) -> None:
    trades = root / "live-trades.jsonl"
    micro = root / "micro"
    tasks = [
        asyncio.create_task(collect(trades, ["BTC", "ETH"]), name="trade-collector"),
        asyncio.create_task(collect_micro(micro, ["BTC", "ETH"], 1000), name="micro-collector"),
    ]
    try:
        await asyncio.sleep(duration_sec)
        for task in tasks:
            if task.done() and not task.cancelled():
                exc = task.exception()
                if exc is not None:
                    raise RuntimeError(f"{task.get_name()} stopped early: {exc}")
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for task, result in zip(tasks, results):
            if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError):
                raise RuntimeError(f"{task.get_name()} failed: {result}") from result


def run_smoke(duration_sec: float, root: Path) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    asyncio.run(collect_for(duration_sec, root))
    synchronized_dir = root / "synchronized"
    sync = build_pattern_dataset(
        trades_path=root / "live-trades.jsonl",
        micro_dir=root / "micro",
        output_dir=synchronized_dir,
        window_sec=30,
        # Deliberately short smoke warm-up. NOT research configuration.
        min_history=2,
        extreme_percentile=0.90,
        max_stale_ms=5000,
        max_event_lag_ms=5000,
        max_book_stale_ms=5000,
    )
    for asset in ("BTC", "ETH"):
        if int(sync["quality_pass_windows_by_asset"].get(asset, 0)) < 1:
            raise RuntimeError(
                f"no synchronized quality-passed {asset} window; rejects="
                f"{sync['quality_reject_windows_by_asset'].get(asset, 0)}"
            )

    study = run_catalog_study(
        events_path=synchronized_dir / "pattern_events.jsonl",
        windows_path=synchronized_dir / "pattern_windows.jsonl",
        output_dir=root / "catalog-study",
        bootstrap_draws=100,
        signflip_draws=200,
    )
    payload = {
        "smoke_only": True,
        "research_claim_allowed": False,
        "duration_sec": duration_sec,
        "synchronized": sync,
        "catalog_group_count": len(study["groups"]),
    }
    (root / "pattern-smoke-summary.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
    )
    return payload


def main() -> None:
    p = argparse.ArgumentParser(description="End-to-end live smoke for synchronized Flow Lab patterns")
    p.add_argument("--duration-sec", type=float, default=150.0)
    p.add_argument("--output-dir", default="results/flow_lab/live-pattern-smoke")
    args = p.parse_args()
    if args.duration_sec < 65:
        raise SystemExit("--duration-sec must be >= 65 to cross multiple 30s windows")
    result = run_smoke(args.duration_sec, Path(args.output_dir))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

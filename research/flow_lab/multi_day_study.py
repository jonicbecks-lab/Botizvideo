from __future__ import annotations

import argparse
import json
from datetime import date, timedelta, timezone, datetime
from itertools import chain
from pathlib import Path

from .archive_study import _count_classes, _summarize, stream_buckets
from .event_study import EventStudyConfig, build_event_table, independent_events
from .historical import binance_vision_aggtrades_url, download_file, iter_binance_aggtrades_zip
from .metrics import combine_venues


def _days(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def _events_per_day(events: list[dict]) -> dict[str, dict[str, int]]:
    per_day: dict[str, dict[str, int]] = {}
    for e in events:
        klass = str(e["response_class"])
        day = datetime.fromtimestamp(int(e["start_ms"]) / 1000, tz=timezone.utc).date().isoformat()
        bucket = per_day.setdefault(day, {})
        bucket[klass] = bucket.get(klass, 0) + 1
    return per_day


def study_range(symbol: str, start: date, end: date, workdir: Path,
                window_sec: int = 30, extreme_percentile: float = 0.99) -> dict:
    if end < start:
        raise ValueError("end must be >= start")
    archives: list[tuple[date, Path]] = []
    for day in _days(start, end):
        url = binance_vision_aggtrades_url(symbol, day, "perp")
        path = workdir / url.rsplit("/", 1)[-1]
        if not path.exists():
            download_file(url, path)
        archives.append((day, path))

    events_iter = chain.from_iterable(
        iter_binance_aggtrades_zip(path, symbol, "perp") for _, path in archives
    )
    buckets = list(stream_buckets(events_iter, window_sec * 1000))
    composite = combine_venues(buckets)
    refs = [b.to_dict() for b in buckets]
    cfg = EventStudyConfig(
        extreme_percentile=extreme_percentile,
        min_history=200,
        horizons_buckets=(1, 2, 4, 10, 30, 60, 120),
        family_cooldown_buckets=4,
    )
    events = build_event_table(composite, refs, cfg)
    independent = independent_events(events)

    return {
        "symbol": symbol,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "days": (end - start).days + 1,
        "source": "Binance USD-M perpetual aggTrades public archive",
        "window_sec": window_sec,
        "extreme_percentile_past_only": extreme_percentile,
        "min_history_buckets": cfg.min_history,
        "family_cooldown_buckets": cfg.family_cooldown_buckets,
        "vol_absorption_units": cfg.vol_absorption_units,
        "vol_continuation_units": cfg.vol_continuation_units,
        "bucket_count": len(buckets),
        "raw_event_count": len(events),
        "independent_event_count": len(independent),
        "response_class_counts_raw_v2": _count_classes(events),
        "response_class_counts_independent_v2": _count_classes(independent),
        "response_class_counts_raw_legacy": _count_classes(events, "response_class_legacy"),
        "events_per_day_by_class_raw_v2": _events_per_day(events),
        "events_per_day_by_class_independent_v2": _events_per_day(independent),
        "horizon_seconds": {str(h): h * window_sec for h in cfg.horizons_buckets},
        "groups": _summarize(independent, cfg.horizons_buckets),
        "groups_raw": _summarize(events, cfg.horizons_buckets),
        "interpretation": "fixed-parameter v2 multi-day diagnostic with causal clustering and past-only volatility normalization; not a trading edge claim",
    }


def main() -> None:
    p = argparse.ArgumentParser(description="Fixed-parameter multi-day Binance flow event study v2")
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--assets", nargs="+", default=["BTC", "ETH"])
    p.add_argument("--window-sec", type=int, default=30)
    p.add_argument("--extreme-percentile", type=float, default=0.99)
    p.add_argument("--workdir", default="data/flow/archive-multiday")
    p.add_argument("--output", default="results/flow_lab/multiday.json")
    args = p.parse_args()
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    workdir = Path(args.workdir); workdir.mkdir(parents=True, exist_ok=True)
    output = Path(args.output); output.parent.mkdir(parents=True, exist_ok=True)
    results = [study_range(f"{a.upper()}USDT", start, end, workdir, args.window_sec,
                           args.extreme_percentile) for a in args.assets]
    payload = {"study": "FLOW_BINANCE_V2_MULTIDAY", "results": results}
    output.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

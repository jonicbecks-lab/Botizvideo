from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from .catalog_study import run_catalog_study
from .collector import collect
from .metrics import aggregate_events, combine_venues
from .schema import TradeEvent
from .window_replay import build_pattern_dataset


def _load_jsonl(path: Path) -> list[TradeEvent]:
    out = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                out.append(TradeEvent(**json.loads(line)))
    return out


def cmd_collect(args: argparse.Namespace) -> None:
    asyncio.run(collect(args.output, args.assets))


def cmd_summarize(args: argparse.Namespace) -> None:
    events = _load_jsonl(Path(args.input))
    buckets = aggregate_events(events, bucket_ms=args.window * 1000)
    rows = combine_venues(buckets)
    print(json.dumps(rows[-args.tail:], indent=2))


def cmd_build_patterns(args: argparse.Namespace) -> None:
    summary = build_pattern_dataset(
        trades_path=Path(args.trades),
        micro_dir=Path(args.micro_dir),
        output_dir=Path(args.output_dir),
        window_sec=args.window,
        min_history=args.min_history,
        extreme_percentile=args.extreme_percentile,
        max_stale_ms=args.max_stale_ms,
        max_event_lag_ms=args.max_event_lag_ms,
        max_book_stale_ms=args.max_book_stale_ms,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


def cmd_study_patterns(args: argparse.Namespace) -> None:
    payload = run_catalog_study(
        events_path=Path(args.events),
        windows_path=Path(args.windows),
        output_dir=Path(args.output_dir),
        bootstrap_draws=args.bootstrap_draws,
        signflip_draws=args.signflip_draws,
    )
    print(json.dumps(payload, indent=2, sort_keys=True))


def main() -> None:
    p = argparse.ArgumentParser(description="BTC/ETH multi-venue signed flow research")
    sub = p.add_subparsers(required=True)
    c = sub.add_parser("collect", help="collect public trade streams to append-only JSONL")
    c.add_argument("--assets", nargs="+", default=["BTC", "ETH"])
    c.add_argument("--output", default="data/flow/live-trades.jsonl")
    c.set_defaults(func=cmd_collect)
    s = sub.add_parser("summarize", help="aggregate a normalized JSONL file")
    s.add_argument("--input", required=True)
    s.add_argument("--window", type=int, default=30, help="bucket seconds")
    s.add_argument("--tail", type=int, default=20)
    s.set_defaults(func=cmd_summarize)
    b = sub.add_parser(
        "build-patterns",
        help="synchronize trades/microstructure into quality-gated multi-pattern events",
    )
    b.add_argument("--trades", default="data/flow/live-trades.jsonl")
    b.add_argument("--micro-dir", default="data/flow/micro")
    b.add_argument("--output-dir", default="results/flow_lab/synchronized-patterns")
    b.add_argument("--window", type=int, default=30, help="bucket seconds")
    b.add_argument("--min-history", type=int, default=200)
    b.add_argument("--extreme-percentile", type=float, default=0.99)
    b.add_argument("--max-stale-ms", type=int, default=5000)
    b.add_argument("--max-event-lag-ms", type=int, default=5000)
    b.add_argument("--max-book-stale-ms", type=int, default=5000)
    b.set_defaults(func=cmd_build_patterns)
    st = sub.add_parser(
        "study-patterns",
        help="forward-label synchronized pattern events and run catalog statistics",
    )
    st.add_argument("--events", default="results/flow_lab/synchronized-patterns/pattern_events.jsonl")
    st.add_argument("--windows", default="results/flow_lab/synchronized-patterns/pattern_windows.jsonl")
    st.add_argument("--output-dir", default="results/flow_lab/catalog-study")
    st.add_argument("--bootstrap-draws", type=int, default=2000)
    st.add_argument("--signflip-draws", type=int, default=5000)
    st.set_defaults(func=cmd_study_patterns)
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

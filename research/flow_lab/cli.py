from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from .collector import collect
from .metrics import aggregate_events, combine_venues
from .schema import TradeEvent


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
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

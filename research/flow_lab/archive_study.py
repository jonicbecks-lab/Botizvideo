from __future__ import annotations

import argparse
import json
import statistics
from datetime import date
from pathlib import Path
from typing import Iterable, Iterator

from .event_study import EventStudyConfig, build_event_table
from .historical import binance_vision_aggtrades_url, download_file, iter_binance_aggtrades_zip
from .metrics import combine_venues
from .schema import FlowBucket, TradeEvent


def stream_buckets(events: Iterable[TradeEvent], bucket_ms: int) -> Iterator[FlowBucket]:
    current_start = None
    exchange = market = asset = None
    count = 0
    buy = sell = 0.0
    op = cp = hi = lo = 0.0
    last_ts = -1

    def emit() -> FlowBucket | None:
        if current_start is None or count == 0:
            return None
        gross, delta = buy + sell, buy - sell
        return FlowBucket(
            exchange=exchange, market=market, asset=asset,
            start_ms=current_start, end_ms=current_start + bucket_ms,
            trade_count=count, buy_usd=buy, sell_usd=sell, delta_usd=delta, gross_usd=gross,
            flow_ratio=delta / gross if gross else 0.0,
            open_price=op, close_price=cp, high_price=hi, low_price=lo,
            return_bps=((cp / op) - 1.0) * 10000.0 if op else 0.0,
        )

    for e in events:
        if e.ts_ms < last_ts:
            raise ValueError("archive trades are not sorted by timestamp")
        last_ts = e.ts_ms
        start = (e.ts_ms // bucket_ms) * bucket_ms
        if current_start is None:
            current_start = start
            exchange, market, asset = e.exchange, e.market, e.asset
            op = cp = hi = lo = e.price
        elif start != current_start:
            row = emit()
            if row:
                yield row
            current_start = start
            exchange, market, asset = e.exchange, e.market, e.asset
            count = 0; buy = sell = 0.0
            op = cp = hi = lo = e.price
        count += 1
        cp = e.price
        hi = max(hi, e.price); lo = min(lo, e.price)
        if e.taker_side == "buy":
            buy += e.notional_usd
        else:
            sell += e.notional_usd
    row = emit()
    if row:
        yield row


def _summarize(events: list[dict], horizons: tuple[int, ...]) -> list[dict]:
    out = []
    groups: dict[tuple[str, str], list[dict]] = {}
    for e in events:
        side = "buy" if float(e["delta_usd"]) > 0 else "sell"
        groups.setdefault((str(e["response_class"]), side), []).append(e)
    for (klass, side), rows in sorted(groups.items()):
        base = {"response_class": klass, "flow_side": side, "n": len(rows)}
        for h in horizons:
            vals = [float(r[f"future_{h}b_in_flow_direction_bps"]) for r in rows
                    if r.get(f"future_{h}b_in_flow_direction_bps") is not None]
            if vals:
                mean_in_flow = sum(vals) / len(vals)
                base[f"h{h}_n"] = len(vals)
                base[f"h{h}_mean_in_flow_bps"] = mean_in_flow
                base[f"h{h}_median_in_flow_bps"] = statistics.median(vals)
                base[f"h{h}_flow_direction_positive_rate"] = sum(v > 0 for v in vals) / len(vals)
                # For absorption, reversal is the economically relevant sign:
                # sell-flow reversal = price rises; buy-flow reversal = price falls.
                base[f"h{h}_mean_reversal_bps"] = -mean_in_flow
                base[f"h{h}_reversal_positive_rate"] = sum(v < 0 for v in vals) / len(vals)
        out.append(base)
    return out


def study_one(symbol: str, day: date, workdir: Path, window_sec: int,
              extreme_percentile: float = 0.99) -> dict:
    url = binance_vision_aggtrades_url(symbol, day, "perp")
    archive = workdir / url.rsplit("/", 1)[-1]
    download_file(url, archive)
    buckets = list(stream_buckets(iter_binance_aggtrades_zip(archive, symbol, "perp"), window_sec * 1000))
    composite = combine_venues(buckets)
    refs = [b.to_dict() for b in buckets]
    cfg = EventStudyConfig(
        extreme_percentile=extreme_percentile,
        min_history=200,
        horizons_buckets=(1, 2, 4, 10, 30, 60, 120),
    )
    events = build_event_table(composite, refs, cfg)
    class_counts: dict[str, int] = {}
    for e in events:
        class_counts[e["response_class"]] = class_counts.get(e["response_class"], 0) + 1
    return {
        "symbol": symbol,
        "date": day.isoformat(),
        "source": "Binance USD-M perpetual aggTrades public archive",
        "window_sec": window_sec,
        "extreme_percentile_past_only": extreme_percentile,
        "min_history_buckets": cfg.min_history,
        "bucket_count": len(buckets),
        "event_count": len(events),
        "response_class_counts": class_counts,
        "horizon_seconds": {str(h): h * window_sec for h in cfg.horizons_buckets},
        "groups": _summarize(events, cfg.horizons_buckets),
        "interpretation": "pilot/descriptive only; no parameter selection or edge claim from this day",
    }


def main() -> None:
    p = argparse.ArgumentParser(description="Single-day Binance flow event-study pilot")
    p.add_argument("--date", required=True)
    p.add_argument("--assets", nargs="+", default=["BTC", "ETH"])
    p.add_argument("--window-sec", type=int, default=30)
    p.add_argument("--extreme-percentile", type=float, default=0.99)
    p.add_argument("--workdir", default="data/flow/archive-pilot")
    p.add_argument("--output", default="results/flow_lab/pilot.json")
    args = p.parse_args()
    day = date.fromisoformat(args.date)
    workdir = Path(args.workdir); workdir.mkdir(parents=True, exist_ok=True)
    output = Path(args.output); output.parent.mkdir(parents=True, exist_ok=True)
    rows = [study_one(f"{a.upper()}USDT", day, workdir, args.window_sec, args.extreme_percentile)
            for a in args.assets]
    payload = {"study": "FLOW_BINANCE_BOOTSTRAP_PILOT", "results": rows}
    output.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

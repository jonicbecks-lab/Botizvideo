from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Iterable, Mapping

from .event_study import EventStudyConfig, RollingMedianAbsReturn
from .families import PatternFamilyClusterer
from .metrics import combine_venues
from .online_response import OnlineResponseTracker, median_reference
from .pattern_engine import (
    BookEvidenceWindow,
    ExhaustionWindow,
    LiquidationWindow,
    OIWindow,
    PatternEngine,
)
from .quality import StreamHealth, quality_gate
from .runtime_health import trade_stream_id
from .schema import FlowBucket, TradeEvent


@dataclass(slots=True)
class _TradeAccumulator:
    exchange: str
    market: str
    asset: str
    start_ms: int
    end_ms: int
    trade_count: int = 0
    buy_usd: float = 0.0
    sell_usd: float = 0.0
    open_ts_ms: int | None = None
    close_ts_ms: int | None = None
    open_price: float = 0.0
    close_price: float = 0.0
    high_price: float = float("-inf")
    low_price: float = float("inf")

    def add(self, event: TradeEvent) -> None:
        self.trade_count += 1
        if event.taker_side == "buy":
            self.buy_usd += float(event.notional_usd)
        else:
            self.sell_usd += float(event.notional_usd)
        if self.open_ts_ms is None or event.ts_ms < self.open_ts_ms:
            self.open_ts_ms = int(event.ts_ms)
            self.open_price = float(event.price)
        if self.close_ts_ms is None or event.ts_ms >= self.close_ts_ms:
            self.close_ts_ms = int(event.ts_ms)
            self.close_price = float(event.price)
        self.high_price = max(self.high_price, float(event.price))
        self.low_price = min(self.low_price, float(event.price))

    def finish(self) -> FlowBucket:
        gross = self.buy_usd + self.sell_usd
        delta = self.buy_usd - self.sell_usd
        return FlowBucket(
            exchange=self.exchange,
            market=self.market,
            asset=self.asset,
            start_ms=self.start_ms,
            end_ms=self.end_ms,
            trade_count=self.trade_count,
            buy_usd=self.buy_usd,
            sell_usd=self.sell_usd,
            delta_usd=delta,
            gross_usd=gross,
            flow_ratio=delta / gross if gross else 0.0,
            open_price=self.open_price,
            close_price=self.close_price,
            high_price=self.high_price,
            low_price=self.low_price,
            return_bps=((self.close_price / self.open_price) - 1.0) * 10000.0
            if self.open_price else 0.0,
        )


def _jsonl(path: Path) -> Iterable[dict]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def _bucket_start(ts_ms: int, bucket_ms: int) -> int:
    return (int(ts_ms) // bucket_ms) * bucket_ms


def _event_time(row: Mapping) -> int:
    ts = int(row.get("ts_ms") or 0)
    if ts > 0:
        return ts
    return int(row.get("recv_ts_ms") or row.get("emit_ts_ms") or 0)


def _required_streams(asset: str) -> tuple[str, ...]:
    sources = (
        ("binance", "spot"), ("bybit", "spot"), ("okx", "spot"),
        ("binance", "perp"), ("bybit", "perp"), ("okx", "perp"),
        ("hyperliquid", "perp"),
    )
    return tuple(trade_stream_id(exchange, market, asset) for exchange, market in sources)


def _load_trade_buckets(path: Path, bucket_ms: int):
    acc: dict[tuple[str, int, str, str], _TradeAccumulator] = {}
    health_obs: dict[tuple[str, int, str], tuple[int, int]] = {}
    for row in _jsonl(path):
        event = TradeEvent(**row)
        start = _bucket_start(event.ts_ms, bucket_ms)
        key = (event.asset, start, event.exchange, event.market)
        cur = acc.get(key)
        if cur is None:
            cur = _TradeAccumulator(
                event.exchange, event.market, event.asset, start, start + bucket_ms
            )
            acc[key] = cur
        cur.add(event)
        stream_id = trade_stream_id(event.exchange, event.market, event.asset)
        recv = int(event.recv_ts_ms if event.recv_ts_ms is not None else event.ts_ms)
        hk = (event.asset, start, stream_id)
        prev = health_obs.get(hk)
        if prev is None or recv >= prev[1]:
            health_obs[hk] = (int(event.ts_ms), recv)

    grouped: dict[tuple[str, int], list[FlowBucket]] = defaultdict(list)
    for (asset, start, _, _), row in acc.items():
        grouped[(asset, start)].append(row.finish())
    return grouped, health_obs


def _load_latest_context(path: Path, bucket_ms: int) -> dict[tuple[str, int], dict[str, dict]]:
    out: dict[tuple[str, int], dict[str, dict]] = defaultdict(dict)
    for row in _jsonl(path):
        if row.get("open_interest_usd") is None and row.get("open_interest_base") is None:
            continue
        ts = _event_time(row)
        if ts <= 0:
            continue
        asset = str(row.get("asset", "")).upper()
        exchange = str(row.get("exchange", ""))
        key = (asset, _bucket_start(ts, bucket_ms))
        prev = out[key].get(exchange)
        if prev is None or _event_time(prev) <= ts:
            out[key][exchange] = row
    return out


def _load_latest_books(path: Path, bucket_ms: int) -> dict[tuple[str, int, str, str], dict]:
    out: dict[tuple[str, int, str, str], dict] = {}
    for row in _jsonl(path):
        ts = int(row.get("emit_ts_ms") or row.get("recv_ts_ms") or row.get("ts_ms") or 0)
        if ts <= 0:
            continue
        key = (
            str(row.get("asset", "")).upper(),
            _bucket_start(ts, bucket_ms),
            str(row.get("exchange", "")),
            str(row.get("market", "")),
        )
        prev = out.get(key)
        prev_ts = int((prev or {}).get("emit_ts_ms") or (prev or {}).get("recv_ts_ms") or 0)
        if prev is None or ts >= prev_ts:
            out[key] = row
    return out


def _load_liquidations(path: Path, bucket_ms: int) -> dict[tuple[str, int], dict[str, float]]:
    out: dict[tuple[str, int], dict[str, float]] = defaultdict(lambda: {"long": 0.0, "short": 0.0})
    for row in _jsonl(path):
        ts = _event_time(row)
        if ts <= 0:
            continue
        key = (str(row.get("asset", "")).upper(), _bucket_start(ts, bucket_ms))
        side = str(row.get("liquidated_side", "")).lower()
        if side in {"long", "short"}:
            out[key][side] += max(0.0, float(row.get("notional_usd", 0.0)))
    return out


def _oi_value(row: Mapping) -> tuple[str, float] | None:
    if row.get("open_interest_usd") is not None:
        return "usd", float(row["open_interest_usd"])
    base = row.get("open_interest_base")
    if base is None:
        return None
    mark = row.get("mark_price")
    if mark is not None:
        return "usd", float(base) * float(mark)
    return "base", float(base)


def _write_jsonl(path: Path, rows: Iterable[Mapping]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(dict(row), separators=(",", ":"), sort_keys=True) + "\n")


def build_pattern_dataset(
    *,
    trades_path: Path,
    micro_dir: Path,
    output_dir: Path,
    window_sec: int = 30,
    min_history: int = 200,
    extreme_percentile: float = 0.99,
    max_stale_ms: int = 5000,
    max_event_lag_ms: int = 5000,
    max_book_stale_ms: int = 5000,
) -> dict:
    bucket_ms = int(window_sec) * 1000
    if bucket_ms <= 0:
        raise ValueError("window_sec must be positive")

    grouped, health_obs = _load_trade_buckets(trades_path, bucket_ms)
    contexts = _load_latest_context(micro_dir / "derivatives_context.jsonl", bucket_ms)
    books = _load_latest_books(micro_dir / "book_features.jsonl", bucket_ms)
    liquidations = _load_liquidations(micro_dir / "liquidations.jsonl", bucket_ms)

    cfg = EventStudyConfig(
        extreme_percentile=extreme_percentile,
        min_history=min_history,
        horizons_buckets=(1, 2, 4, 10, 30, 60, 120),
        family_cooldown_buckets=4,
    )
    response_tracker = OnlineResponseTracker(cfg, bucket_ms=bucket_ms)
    family_clusterer = PatternFamilyClusterer(bucket_ms=bucket_ms, cooldown_buckets=4)
    engine = PatternEngine(min_history=min_history)
    health: dict[str, StreamHealth] = {}
    book_scales: dict[tuple[str, str, str], RollingMedianAbsReturn] = {}
    prev_oi: dict[tuple[str, str], tuple[str, float]] = {}

    event_rows: list[dict] = []
    window_rows: list[dict] = []
    quality_reasons: Counter[str] = Counter()
    event_counts: Counter[str] = Counter()
    independent_counts: Counter[str] = Counter()
    label_counts: Counter[str] = Counter()
    independent_label_counts: Counter[str] = Counter()
    pass_count: Counter[str] = Counter()
    reject_count: Counter[str] = Counter()

    starts_by_asset: dict[str, list[int]] = defaultdict(list)
    for asset, start in grouped:
        starts_by_asset[asset].append(start)

    for asset in sorted(starts_by_asset):
        for start in sorted(set(starts_by_asset[asset])):
            rows = sorted(grouped[(asset, start)], key=lambda b: (b.market, b.exchange))
            end_ms = start + bucket_ms
            current_recv: list[int] = []
            for stream_id in _required_streams(asset):
                obs = health_obs.get((asset, start, stream_id))
                if obs is None:
                    continue
                h = health.get(stream_id)
                if h is None:
                    h = StreamHealth(stream_id)
                    health[stream_id] = h
                h.observe(obs[0], obs[1])
                current_recv.append(obs[1])
            now_ms = max([end_ms, *current_recv])
            required = _required_streams(asset)
            gate = quality_gate(
                health,
                required,
                now_ms,
                max_stale_ms=max_stale_ms,
                max_event_lag_ms=max_event_lag_ms,
            )
            if not gate["quality_pass"]:
                reject_count[asset] += 1
                for reason in gate["reasons"]:
                    quality_reasons[reason.split(":", 1)[0]] += 1
                result = engine.evaluate(
                    now_ms=now_ms,
                    stream_health=health,
                    required_streams=required,
                    max_stale_ms=max_stale_ms,
                    max_event_lag_ms=max_event_lag_ms,
                )
                window_rows.append({
                    "asset": asset,
                    "start_ms": start,
                    "end_ms": end_ms,
                    "quality": result["quality"],
                    "event_count": 0,
                    "events": [],
                })
                continue

            pass_count[asset] += 1
            spot = [b for b in rows if b.market == "spot"]
            perp = [b for b in rows if b.market == "perp"]
            response_events: list[dict] = []
            exhaustion_windows: list[ExhaustionWindow] = []
            response_observations: dict[str, dict] = {}

            for market, market_rows in (("spot", spot), ("perp", perp)):
                if not market_rows:
                    continue
                combined = combine_venues(market_rows)[0]
                ref_return, ref_close = median_reference(market_rows)
                obs = response_tracker.observe(
                    combined,
                    reference_return_bps=ref_return,
                    reference_close=ref_close,
                )
                response_observations[market] = obs
                if obs.get("response_event") is not None:
                    response_events.append(obs["response_event"])
                exhaustion_windows.append(ExhaustionWindow(
                    asset=asset,
                    market=market,
                    bucket_index=start // bucket_ms,
                    delta_usd=float(combined["delta_usd"]),
                    abs_delta_percentile_past_only=obs.get("abs_delta_percentile_past_only"),
                    signed_impact_units_past_only=obs.get("signed_impact_units_past_only"),
                    start_ms=start,
                ))

            by_exchange_perp = {b.exchange: b for b in perp}
            oi_windows: list[OIWindow] = []
            for exchange, ctx in contexts.get((asset, start), {}).items():
                flow = by_exchange_perp.get(exchange)
                if flow is None:
                    continue
                cur = _oi_value(ctx)
                if cur is None:
                    continue
                key = (exchange, asset)
                prev = prev_oi.get(key)
                prev_oi[key] = cur
                if prev is None or prev[0] != cur[0]:
                    continue
                if cur[0] == "usd":
                    delta_oi_usd = cur[1] - prev[1]
                else:
                    delta_oi_usd = (cur[1] - prev[1]) * float(flow.close_price)
                oi_windows.append(OIWindow(
                    exchange=exchange,
                    asset=asset,
                    delta_oi_usd=delta_oi_usd,
                    price_return_bps=float(flow.return_bps),
                    delta_flow_usd=float(flow.delta_usd),
                    start_ms=start,
                ))

            book_windows: list[BookEvidenceWindow] = []
            for flow in rows:
                scale_key = (flow.exchange, flow.market, asset)
                scaler = book_scales.get(scale_key)
                if scaler is None:
                    scaler = RollingMedianAbsReturn(cfg.history_maxlen, cfg.min_history)
                    book_scales[scale_key] = scaler
                prior_scale = scaler.scale_then_update(float(flow.return_bps))
                book = books.get((asset, start, flow.exchange, flow.market))
                if book is None:
                    continue
                book_recv = int(book.get("recv_ts_ms") or book.get("emit_ts_ms") or 0)
                if book_recv <= 0 or max(0, now_ms - book_recv) > max_book_stale_ms:
                    continue
                book_windows.append(BookEvidenceWindow(
                    exchange=flow.exchange,
                    market=flow.market,
                    asset=asset,
                    delta_usd=float(flow.delta_usd),
                    return_bps=float(flow.return_bps),
                    impact_scale_bps=prior_scale,
                    depth_change=book,
                    start_ms=start,
                ))

            liquidation_window = None
            if perp:
                # Bybit orderbook freshness proxies the public-linear connection that
                # also carries allLiquidation. This lets zero-liquidation windows enter
                # the past-only baseline only while that connection is demonstrably live.
                bybit_book = books.get((asset, start, "bybit", "perp"))
                bybit_recv = int((bybit_book or {}).get("recv_ts_ms") or
                                 (bybit_book or {}).get("emit_ts_ms") or 0)
                liq_feed_healthy = (
                    bybit_book is not None
                    and bybit_recv > 0
                    and max(0, now_ms - bybit_recv) <= max_book_stale_ms
                )
                if liq_feed_healthy:
                    liq = liquidations.get((asset, start), {"long": 0.0, "short": 0.0})
                    perp_combined = combine_venues(perp)[0]
                    perp_return, _ = median_reference(perp)
                    liquidation_window = LiquidationWindow(
                        asset=asset,
                        market="perp",
                        long_liq_usd=float(liq["long"]),
                        short_liq_usd=float(liq["short"]),
                        price_return_bps=perp_return,
                        delta_flow_usd=float(perp_combined["delta_usd"]),
                        start_ms=start,
                    )

            result = engine.evaluate(
                now_ms=now_ms,
                stream_health=health,
                required_streams=required,
                spot_buckets=spot,
                perp_buckets=perp,
                response_events=response_events,
                oi_windows=oi_windows,
                liquidation_window=liquidation_window,
                exhaustion_windows=exhaustion_windows,
                book_windows=book_windows,
                max_stale_ms=max_stale_ms,
                max_event_lag_ms=max_event_lag_ms,
            )

            compact_events: list[dict] = []
            for raw_event in result["events"]:
                event = dict(raw_event)
                event.setdefault("asset", asset)
                event.setdefault("start_ms", start)
                event["evaluation_ts_ms"] = now_ms
                event = family_clusterer.observe(event, default_start_ms=start)
                event_rows.append(event)
                family = str(event.get("pattern_family", "unknown"))
                label = str(event.get("pattern_label", family))
                compact_events.append({
                    "pattern_family": family,
                    "pattern_label": label,
                    "is_independent_event": bool(event["is_independent_event"]),
                })
                event_counts[family] += 1
                label_counts[label] += 1
                if event["is_independent_event"]:
                    independent_counts[family] += 1
                    independent_label_counts[label] += 1

            window_rows.append({
                "asset": asset,
                "start_ms": start,
                "end_ms": end_ms,
                "quality": result["quality"],
                "event_count": len(compact_events),
                "events": compact_events,
                "response_observations": response_observations,
                "book_evidence": result["diagnostics"].get("book_evidence", []),
            })

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(output_dir / "pattern_events.jsonl", event_rows)
    _write_jsonl(output_dir / "pattern_windows.jsonl", window_rows)
    summary = {
        "study": "FLOW_LAB_SYNCHRONIZED_PATTERN_REPLAY",
        "trades_path": str(trades_path),
        "micro_dir": str(micro_dir),
        "window_sec": window_sec,
        "min_history": min_history,
        "extreme_percentile": extreme_percentile,
        "event_family_cooldown_buckets": 4,
        "quality_pass_windows_by_asset": dict(pass_count),
        "quality_reject_windows_by_asset": dict(reject_count),
        "quality_reject_reason_kinds": dict(quality_reasons),
        "pattern_event_counts_raw": dict(event_counts),
        "pattern_event_counts_independent": dict(independent_counts),
        "pattern_label_counts_raw": dict(label_counts),
        "pattern_label_counts_independent": dict(independent_label_counts),
        "event_count_raw": len(event_rows),
        "event_count_independent": sum(bool(e["is_independent_event"]) for e in event_rows),
        "window_count": len(window_rows),
        "outputs": {
            "events": str(output_dir / "pattern_events.jsonl"),
            "windows": str(output_dir / "pattern_windows.jsonl"),
        },
        "notes": [
            "All stateful pattern baselines update only after the trade Data Quality Gate passes.",
            "All pattern families use causal first-event clustering with a fixed 2-minute cooldown.",
            "Response price is the median same-window return across active venues.",
            "Raw USD summed flow remains the response-pattern baseline; normalized cross-venue patterns are evaluated separately.",
            "Liquidation zero-windows are learned only when the Bybit perp websocket is proxied healthy by a fresh orderbook row.",
            "Forward returns are not computed here; this dataset contains trigger-time information only.",
        ],
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8"
    )
    return summary


def main() -> None:
    p = argparse.ArgumentParser(description="Build synchronized multi-pattern Flow Lab dataset")
    p.add_argument("--trades", default="data/flow/live-trades.jsonl")
    p.add_argument("--micro-dir", default="data/flow/micro")
    p.add_argument("--output-dir", default="results/flow_lab/synchronized-patterns")
    p.add_argument("--window-sec", type=int, default=30)
    p.add_argument("--min-history", type=int, default=200)
    p.add_argument("--extreme-percentile", type=float, default=0.99)
    p.add_argument("--max-stale-ms", type=int, default=5000)
    p.add_argument("--max-event-lag-ms", type=int, default=5000)
    p.add_argument("--max-book-stale-ms", type=int, default=5000)
    args = p.parse_args()
    summary = build_pattern_dataset(
        trades_path=Path(args.trades),
        micro_dir=Path(args.micro_dir),
        output_dir=Path(args.output_dir),
        window_sec=args.window_sec,
        min_history=args.min_history,
        extreme_percentile=args.extreme_percentile,
        max_stale_ms=args.max_stale_ms,
        max_event_lag_ms=args.max_event_lag_ms,
        max_book_stale_ms=args.max_book_stale_ms,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

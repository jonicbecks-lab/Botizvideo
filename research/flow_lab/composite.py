from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
import math
from typing import Iterable

from .metrics import RollingNormalizer
from .schema import FlowBucket


@dataclass(slots=True)
class EqualVenueComposite:
    """Past-only equal-venue normalized flow composite.

    Raw USD aggregation remains a baseline elsewhere. This composite prevents the
    highest-volume venue from mechanically dominating by scoring each venue's signed
    flow_ratio against that venue's own prior history, then averaging available
    standardized scores with equal weight. No discretionary exchange weights are used.
    """

    min_history: int = 200
    history_maxlen: int = 20000
    _normalizers: dict[tuple[str, str, str], RollingNormalizer] = field(default_factory=dict)

    def _normalizer(self, exchange: str, market: str, asset: str) -> RollingNormalizer:
        key = (exchange, market, asset)
        if key not in self._normalizers:
            self._normalizers[key] = RollingNormalizer(
                maxlen=self.history_maxlen, min_history=self.min_history
            )
        return self._normalizers[key]

    def combine(self, buckets: Iterable[FlowBucket]) -> list[dict]:
        groups: dict[tuple[str, str, int, int], list[FlowBucket]] = defaultdict(list)
        for b in buckets:
            groups[(b.asset, b.market, b.start_ms, b.end_ms)].append(b)

        out: list[dict] = []
        for (asset, market, start_ms, end_ms), rows in sorted(groups.items()):
            venue_rows: list[dict] = []
            for b in sorted(rows, key=lambda x: x.exchange):
                score = self._normalizer(b.exchange, b.market, b.asset).score_then_update(b.flow_ratio)
                venue_rows.append({
                    "exchange": b.exchange,
                    "flow_ratio": b.flow_ratio,
                    "delta_usd": b.delta_usd,
                    "gross_usd": b.gross_usd,
                    "zscore_past_only": score.zscore,
                    "percentile_past_only": score.percentile,
                    "history_n": score.history_n,
                })

            ready = [r for r in venue_rows if r["zscore_past_only"] is not None]
            zscores = [float(r["zscore_past_only"]) for r in ready]
            signs = [1 if r["flow_ratio"] > 0 else -1 if r["flow_ratio"] < 0 else 0 for r in venue_rows]
            active_signs = [s for s in signs if s]
            avg_z = sum(zscores) / len(zscores) if zscores else None
            if len(zscores) >= 2:
                z_mean = float(avg_z)
                dispersion = math.sqrt(sum((z - z_mean) ** 2 for z in zscores) / len(zscores))
            else:
                dispersion = None

            out.append({
                "asset": asset,
                "market": market,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "venue_count": len(venue_rows),
                "normalized_venue_count": len(ready),
                "active_venues": [r["exchange"] for r in venue_rows],
                "normalized_active_venues": [r["exchange"] for r in ready],
                "equal_venue_zscore": avg_z,
                "venue_zscore_dispersion": dispersion,
                "buy_venue_count": sum(s > 0 for s in signs),
                "sell_venue_count": sum(s < 0 for s in signs),
                "sign_consensus": abs(sum(active_signs)) / len(active_signs) if active_signs else 0.0,
                "venue_details": venue_rows,
            })
        return out

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


@dataclass(slots=True)
class PatternFamilyClusterer:
    """Causally cluster repeated catalog events into independent episodes.

    The first observed event is always the independent representative. No future
    strength, PnL or duration is used to choose a family representative. Spot and
    perpetual response families stay separate; venue-specific OI confirmations with
    the same label can collapse into one market-wide episode.
    """

    bucket_ms: int = 30_000
    cooldown_buckets: int = 4
    _state: dict[tuple[str, str, str, str, int], tuple[int, int, int]] = field(default_factory=dict)
    _next_family_id: int = 1

    def __post_init__(self) -> None:
        if self.bucket_ms <= 0:
            raise ValueError("bucket_ms must be positive")
        if self.cooldown_buckets < 0:
            raise ValueError("cooldown_buckets must be >= 0")

    @staticmethod
    def _direction(event: Mapping) -> int:
        explicit = int(event.get("hypothesis_direction", 0) or 0)
        if explicit:
            return 1 if explicit > 0 else -1
        label = str(event.get("pattern_label", ""))
        if label.endswith("_buy") or "buy_" in label or label == "new_longs_building":
            return 1
        if label.endswith("_sell") or "sell_" in label or label == "new_shorts_building":
            return -1
        return 0

    def observe(self, event: Mapping, *, default_start_ms: int | None = None) -> dict:
        out = dict(event)
        raw_start = out.get("start_ms", default_start_ms)
        if raw_start is None:
            raise ValueError("pattern event requires start_ms")
        start_ms = int(raw_start)
        out["start_ms"] = start_ms
        idx = start_ms // self.bucket_ms
        asset = str(out.get("asset", ""))
        market = str(out.get("market", ""))
        family = str(out.get("pattern_family", "unknown"))
        label = str(out.get("pattern_label", family))
        direction = self._direction(out)
        key = (asset, market, family, label, direction)
        prev = self._state.get(key)
        if prev is not None and idx - prev[0] <= self.cooldown_buckets:
            family_id = prev[1]
            rank = prev[2] + 1
            independent = False
        else:
            family_id = self._next_family_id
            self._next_family_id += 1
            rank = 0
            independent = True
        self._state[key] = (idx, family_id, rank)
        out["event_family_id"] = family_id
        out["event_family_rank"] = rank
        out["is_independent_event"] = independent
        out["event_family_cooldown_buckets"] = self.cooldown_buckets
        return out

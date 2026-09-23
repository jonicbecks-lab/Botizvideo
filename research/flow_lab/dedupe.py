from __future__ import annotations

from collections import OrderedDict

from .schema import TradeEvent


class TradeDeduper:
    """Bounded LRU deduplication for reconnect/replay overlap.

    Exchange trade IDs are only compared within exchange+market+symbol. The deduper
    is deliberately finite so a long-running collector cannot grow memory without
    bound. It prevents short reconnect overlaps, not historical-global uniqueness.
    """

    def __init__(self, max_keys: int = 200_000) -> None:
        if max_keys < 1:
            raise ValueError("max_keys must be >= 1")
        self.max_keys = int(max_keys)
        self._seen: OrderedDict[tuple[str, str, str, str], None] = OrderedDict()
        self.accepted_count = 0
        self.duplicate_count = 0
        self.evicted_count = 0

    @staticmethod
    def key(event: TradeEvent) -> tuple[str, str, str, str]:
        return event.exchange, event.market, event.symbol, event.trade_id

    def accept(self, event: TradeEvent) -> bool:
        key = self.key(event)
        if key in self._seen:
            self._seen.move_to_end(key)
            self.duplicate_count += 1
            return False
        self._seen[key] = None
        self.accepted_count += 1
        if len(self._seen) > self.max_keys:
            self._seen.popitem(last=False)
            self.evicted_count += 1
        return True

    def stats(self) -> dict[str, int]:
        return {
            "max_keys": self.max_keys,
            "current_keys": len(self._seen),
            "accepted_count": self.accepted_count,
            "duplicate_count": self.duplicate_count,
            "evicted_count": self.evicted_count,
        }

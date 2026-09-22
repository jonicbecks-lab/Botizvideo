"""Multi-venue BTC/ETH signed trade-flow research utilities."""

from .schema import TradeEvent, FlowBucket
from .metrics import aggregate_events, combine_venues, RollingNormalizer

__all__ = [
    "TradeEvent",
    "FlowBucket",
    "aggregate_events",
    "combine_venues",
    "RollingNormalizer",
]

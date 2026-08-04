"""Production-safe compatibility layer for Galka LIVE.

Hyperliquid normalTpsl grouping is intentionally submitted as one entry/TP pair
per request. A single request containing multiple normalTpsl pairs can be
rejected with ``Trigger order has unexpected type``. The optimized cancellation
path remains inherited from ``CompatibleGalkaLiveEngine``.
"""

from __future__ import annotations

from .hyperliquid_compat import (
    CompatibleGalkaLiveEngine as _OptimizedEngine,
    CompatibleHyperliquidGateway as _BatchGateway,
)
from .hyperliquid_gateway import EntryWithTarget
from .live_ladder import LadderLevel


class SafeCompatibleHyperliquidGateway(_BatchGateway):
    """Use the exchange-tested one-pair normalTpsl submission format."""

    def place_ladder_with_targets(
        self,
        coin: str,
        levels: list[LadderLevel],
        galka_price: float,
        entry_cloids: list[str],
        target_cloids: list[str],
    ) -> list[EntryWithTarget]:
        if not levels or len(levels) != len(entry_cloids) or len(levels) != len(target_cloids):
            from .hyperliquid_gateway import GatewayError

            raise GatewayError("Incomplete ladder batch parameters")

        pairs: list[EntryWithTarget] = []
        for level, entry_cloid, target_cloid in zip(levels, entry_cloids, target_cloids):
            pairs.append(
                super().place_entry_with_target(
                    coin,
                    level,
                    galka_price,
                    entry_cloid,
                    target_cloid,
                )
            )
        return pairs


class SafeCompatibleGalkaLiveEngine(_OptimizedEngine):
    """Keep fast cancellation while using safe pair-wise placement."""

    pass

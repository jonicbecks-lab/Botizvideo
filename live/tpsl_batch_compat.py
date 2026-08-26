from __future__ import annotations

from .hyperliquid_compat import CompatibleGalkaLiveEngine
from .hyperliquid_gateway import EntryWithTarget, GatewayError
from .hyperliquid_safe_compat import SafeCompatibleHyperliquidGateway
from .live_ladder import LadderLevel


_INSTALLED = False


def _place_ladder_batch_pairwise(
    self: SafeCompatibleHyperliquidGateway,
    coin: str,
    levels: list[LadderLevel],
    galka_price: float,
    entry_cloids: list[str],
    target_cloids: list[str],
) -> list[EntryWithTarget]:
    """Submit every entry together with its reduce-only TP using normalTpsl.

    The optimized compatibility engine calls ``place_ladder_batch`` during
    campaign creation. Submitting entry and reduce-only TP children as sixteen
    independent orders can make Hyperliquid reject the TP legs before an entry
    position exists. The exchange-tested ``place_entry_with_target`` path groups
    each parent/TP pair with ``normalTpsl`` and preserves the existing fail-closed
    recovery behaviour if a later pair fails.
    """
    self._require_live_write("place complete GALKA safely")
    normalized = self._coin(coin)
    if not levels or len(levels) != len(entry_cloids) or len(levels) != len(target_cloids):
        raise GatewayError("Batch cloid/level count mismatch")

    pairs: list[EntryWithTarget] = []
    for level, entry_cloid, target_cloid in zip(levels, entry_cloids, target_cloids):
        pairs.append(
            self.place_entry_with_target(
                normalized,
                level,
                float(galka_price),
                entry_cloid,
                target_cloid,
            )
        )
    return pairs


def install() -> None:
    """Restore exchange-valid bracket placement and complete BNB compatibility."""
    global _INSTALLED
    if _INSTALLED:
        return

    SafeCompatibleHyperliquidGateway.place_ladder_batch = _place_ladder_batch_pairwise

    # The production universe is BTC/ETH/BNB. close_near_market indexes this map
    # directly, so BNB must be present even though the normal GALKA exit remains
    # the unchanged reduce-only limit at the GALKA level.
    CompatibleGalkaLiveEngine._NEAR_MARKET_STEPS.pop("SOL", None)
    CompatibleGalkaLiveEngine._NEAR_MARKET_STEPS["BNB"] = 0.01

    _INSTALLED = True

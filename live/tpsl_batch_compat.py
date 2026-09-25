from __future__ import annotations

from typing import Any

from .hyperliquid_compat import CompatibleGalkaLiveEngine
from .hyperliquid_gateway import EntryWithTarget, GatewayError
from .hyperliquid_safe_compat import SafeCompatibleHyperliquidGateway
from .live_ladder import LadderLevel, round_perp_price


_INSTALLED = False
_ORIGINAL_PREPARE_FILL_OWNERS = CompatibleGalkaLiveEngine._prepare_fill_owners


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


def _prepare_fill_owners_with_trigger_children(
    self: CompatibleGalkaLiveEngine,
    campaign: dict[str, Any],
    fills: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Recover ownership when Hyperliquid fills a TP under a new child OID.

    ``normalTpsl`` may report the executed reduce-only TP fill with an OID that is
    different from the trigger OID stored when the GALKA was created. In that
    case the old resolver cannot find the target by OID/CLOID, so the venue is
    already flat while GALKA still thinks the long is open. The result is a false
    recovery and a bogus local PnL that contains the entry fee but misses the
    profitable closing fill.

    We only infer ownership for an economically valid GALKA close:
    - the fill is a sell that closes a long;
    - it belongs to the current campaign time window;
    - its execution price is at or above the rounded immutable GALKA price;
    - it does not close more than the managed long available at that moment;
    - venue closedPnl is non-negative (within floating-point dust).

    Manual/foreign sells below GALKA are deliberately *not* adopted and still
    force the existing recovery path.
    """
    prepared = _ORIGINAL_PREPARE_FILL_OWNERS(self, campaign, fills)
    coin = str(campaign.get("coin") or "")
    if not coin:
        return prepared

    try:
        galka = round_perp_price(
            float(campaign.get("galkaPrice") or 0),
            self.gateway.sz_decimals(coin),
        )
    except Exception:
        return prepared
    if galka <= 0:
        return prepared

    created_ms = int(campaign.get("createdMs") or 0)
    tolerance = self._size_tolerance(coin)
    shadow_managed = max(0.0, float(campaign.get("managedNetSize") or 0))

    for fill in sorted(prepared, key=lambda row: int(row.get("time") or 0)):
        size = abs(float(fill.get("size") or 0))
        if size <= 0:
            continue

        kind = str(fill.get("_ownerKind") or "")
        if kind == "entry" and fill.get("side") == "B":
            shadow_managed += size
            continue
        if kind == "target" and fill.get("side") == "A":
            shadow_managed = max(0.0, shadow_managed - size)
            continue
        if kind:
            continue

        direction = str(fill.get("direction") or "").strip().lower()
        price = float(fill.get("price") or 0)
        closed_pnl = float(fill.get("closedPnl") or 0)
        fill_time = int(fill.get("time") or 0)

        valid_close = (
            fill.get("side") == "A"
            and direction == "close long"
            and fill_time >= created_ms - 2_000
            and price + 1e-9 >= galka
            and closed_pnl >= -1e-9
            and shadow_managed > tolerance
            and size <= shadow_managed + tolerance
        )
        if not valid_close:
            continue

        # A normalTpsl trigger can execute as a venue child order whose OID/CLOID
        # no longer matches the parent stored in campaign state. Mark it as an
        # owned target so the normal accounting path records closedPnl + exit fee.
        fill["_ownerKind"] = "target"
        fill["_ownerLevel"] = 0
        fill["_ownerInference"] = "normalTpsl-child-at-galka"
        shadow_managed = max(0.0, shadow_managed - size)

    return prepared


def install() -> None:
    """Restore exchange-valid bracket placement and complete BNB compatibility."""
    global _INSTALLED
    if _INSTALLED:
        return

    SafeCompatibleHyperliquidGateway.place_ladder_batch = _place_ladder_batch_pairwise
    CompatibleGalkaLiveEngine._prepare_fill_owners = _prepare_fill_owners_with_trigger_children

    # The production universe is BTC/ETH/BNB. close_near_market indexes this map
    # directly, so BNB must be present even though the normal GALKA exit remains
    # the unchanged reduce-only limit at the GALKA level.
    CompatibleGalkaLiveEngine._NEAR_MARKET_STEPS.pop("SOL", None)
    CompatibleGalkaLiveEngine._NEAR_MARKET_STEPS["BNB"] = 0.01

    _INSTALLED = True

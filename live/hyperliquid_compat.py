"""Compatibility names kept for existing launch scripts."""

from .hyperliquid_gateway import HyperliquidGateway
from .live_recovery_fixes import ReliableGalkaLiveEngine


class CompatibleHyperliquidGateway(HyperliquidGateway):
    pass


class CompatibleGalkaLiveEngine(ReliableGalkaLiveEngine):
    pass

"""Compatibility names kept for existing launch scripts.

The strict response and delayed-TP compatibility logic now lives in the main
classes. Keeping these aliases avoids breaking installations that import the old
class names while removing the unsafe unknown-fill heuristic.
"""

from .engine import GalkaLiveEngine
from .hyperliquid_gateway import HyperliquidGateway


class CompatibleHyperliquidGateway(HyperliquidGateway):
    pass


class CompatibleGalkaLiveEngine(GalkaLiveEngine):
    pass

"""Local Hyperliquid live-trading backend for the Galka terminal."""

# Install the GALKA MEM extensions before live.server imports the concrete
# gateway/engine classes. This keeps the hardened base implementation intact
# while layering the user-specific mobile trading workflow on top.
from .mem_protection import install_engine_protection, install_gateway_protection
from .mem_quick_controls import install_quick_controls
from .mem_market_whitelist import install_mem_market_whitelist
from . import mem_gateway as _mem_gateway

_mem_gateway.MemHyperliquidGateway = install_gateway_protection(
    _mem_gateway.MemHyperliquidGateway
)
_mem_gateway.MemHyperliquidGateway = install_mem_market_whitelist(
    _mem_gateway.MemHyperliquidGateway
)

from . import mem_engine as _mem_engine

_mem_engine.GalkaMemEngine = install_engine_protection(_mem_engine.GalkaMemEngine)
_mem_engine.GalkaMemEngine = install_quick_controls(_mem_engine.GalkaMemEngine)

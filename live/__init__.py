"""Local Hyperliquid live-trading backend for the Galka terminal."""

# Install the GALKA MEM protection extension before live.server imports the
# concrete gateway/engine classes. This keeps the hardened base implementation
# untouched while adding the manual break-even/profit-protection layer.
from .mem_protection import install_engine_protection, install_gateway_protection
from .mem_quick_controls import install_quick_controls
from . import mem_gateway as _mem_gateway

_mem_gateway.MemHyperliquidGateway = install_gateway_protection(
    _mem_gateway.MemHyperliquidGateway
)

from . import mem_engine as _mem_engine

_mem_engine.GalkaMemEngine = install_engine_protection(_mem_engine.GalkaMemEngine)
_mem_engine.GalkaMemEngine = install_quick_controls(_mem_engine.GalkaMemEngine)

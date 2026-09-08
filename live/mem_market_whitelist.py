from __future__ import annotations

from typing import Any

# User-selected GALKA MEM universe. Keep this list deliberately small so the
# mobile selector contains only the meme perps the user actually trades.
MEM_MARKET_ORDER = (
    "PONS",
    "CASHCAT",
    "DOGE",
    "kPEPE",
    "FARTCOIN",
    "TRUMP",
    "SPX",
    "kBONK",
    "PEOPLE",
    "kSHIB",
    "WIF",
    "BOME",
    "MELANIA",
    "POPCAT",
    "kFLOKI",
    "PNUT",
)


def install_mem_market_whitelist(base_gateway_cls):
    if getattr(base_gateway_cls, "__galka_mem_market_whitelist__", False):
        return base_gateway_cls

    class WhitelistedMemGateway(base_gateway_cls):
        __galka_mem_market_whitelist__ = True

        def market_catalog(self) -> list[dict[str, Any]]:
            catalog = super().market_catalog()
            by_upper = {str(row.get("name") or "").upper(): row for row in catalog}
            output: list[dict[str, Any]] = []
            for requested in MEM_MARKET_ORDER:
                row = by_upper.get(requested.upper())
                if row is not None:
                    output.append(row)
            return output

    WhitelistedMemGateway.__name__ = base_gateway_cls.__name__
    WhitelistedMemGateway.__qualname__ = base_gateway_cls.__qualname__
    return WhitelistedMemGateway

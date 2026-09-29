from __future__ import annotations

from typing import Any

from . import research_server
from .hyperliquid_gateway import _unified_usdc_values


class AccountModeAwareGateway(research_server.PublicMarketIsolatedGateway):
    """Read collateral from the correct Hyperliquid ledger for abstracted accounts.

    Hyperliquid unified-account and portfolio-margin modes expose balances/holds in
    the spot clearinghouse state. The legacy perp marginSummary can legitimately be
    zero in those modes even while the account still has usable USDC collateral.
    """

    def account_state(self, fresh: bool = False) -> dict[str, Any]:
        result = super().account_state(fresh=fresh)
        mode = str(result.get("accountMode") or "default")
        normalized = mode.lower()

        if normalized == "unifiedaccount":
            # The base gateway already uses spot USDC for unified accounts.
            enriched = dict(result)
            enriched["balanceSource"] = "spot_usdc_unified"
            return self._cache_set("account_state", enriched)

        if normalized != "portfoliomargin":
            enriched = dict(result)
            enriched["balanceSource"] = "perp_margin_summary"
            return self._cache_set("account_state", enriched)

        # Portfolio-margin accounts follow the same balance-location rule as
        # unified accounts. Keep sizing conservative: only immediately usable USDC
        # is counted here; non-USDC collateral is intentionally not converted into
        # extra trading capital.
        spot_state = self._read(
            "spot_user_state_portfolio_margin",
            lambda: self.info.spot_user_state(self.config.account_address),
        )
        account_value, withdrawable = _unified_usdc_values(spot_state)
        enriched = dict(result)
        enriched["accountValue"] = account_value
        enriched["withdrawable"] = withdrawable
        enriched["balanceSource"] = "spot_usdc_portfolio_margin"
        enriched["portfolioMarginConservativeUsdcOnly"] = True
        return self._cache_set("account_state", enriched)


def install() -> None:
    """Install the production gateway override before the persistent server starts."""
    research_server._persistent.SafeCompatibleHyperliquidGateway = AccountModeAwareGateway

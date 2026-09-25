from __future__ import annotations

from typing import Any

from .galka_classic_engine import GalkaClassicGateway
from .hyperliquid_gateway import _unified_usdc_values


class AccountModeCompatibleGalkaClassicGateway(GalkaClassicGateway):
    """Read spendable USDC correctly for Hyperliquid portfolio-margin accounts.

    Hyperliquid documents that unified-account and portfolio-margin balances/holds
    are authoritative in spotClearinghouseState rather than an individual perp
    clearinghouse state. The base gateway already does this for unifiedAccount;
    this wrapper applies the same conservative USDC accounting to portfolioMargin.

    We intentionally use total-minus-hold only. If the account relies on non-USDC
    portfolio collateral, GALKA will under-size/fail closed rather than assume that
    collateral is spendable by this isolated-USDC strategy.
    """

    _SPOT_BALANCE_SOURCE = "spot-usdc-portfolio-margin"

    def account_state(self, fresh: bool = False) -> dict[str, Any]:
        result = super().account_state(fresh=fresh)
        mode = str(result.get("accountMode") or "").strip().lower()
        if mode != "portfoliomargin":
            return result

        # A corrected cached state can be returned directly during normal UI/status
        # polling. Fresh reads always refresh both the perp/position snapshot and
        # the authoritative spot collateral snapshot.
        if not fresh and result.get("balanceSource") == self._SPOT_BALANCE_SOURCE:
            return result

        spot_state = self._read(
            "spot_user_state",
            lambda: self.info.spot_user_state(self.config.account_address),
        )
        account_value, withdrawable = _unified_usdc_values(spot_state)
        corrected = dict(result)
        corrected["accountValue"] = account_value
        corrected["withdrawable"] = withdrawable
        corrected["balanceSource"] = self._SPOT_BALANCE_SOURCE
        return self._cache_set("account_state", corrected)

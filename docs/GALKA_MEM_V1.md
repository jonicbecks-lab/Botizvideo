# GALKA MEM v1

Working rules agreed for the first live meme-perp campaign variant.

## Account and risk

- Uses the same Hyperliquid account/API wallet as hardened GALKA LIVE.
- Isolated margin only.
- Campaign margin budget is user-selectable; current working default/ceiling for the first version is $100.
- Leverage is user-selectable up to the market's allowed maximum (for CASHCAT, use 3x when selected).
- The application must not assume that wallet balance is the campaign risk budget.

## Manual structure

User enters:
- coin/perp,
- leverage,
- GALKA price,
- arbitrary manual entry prices at/above GALKA,
- arbitrary manual entry prices below GALKA,
- campaign margin budget.

Number and spacing of levels are adaptive/manual; there is no fixed 8-level ladder.

## Two independent baskets

Entries are managed as two logical baskets so fills below GALKA never move the take-profit calculation of the upper basket.

Default budget split:
- upper basket (GALKA and above): 1/3 of campaign margin,
- lower basket (below GALKA): 2/3 of campaign margin.

Within each basket, size increases as price gets lower. Weight sequence:

`1, 1.5, 2, 2.5, 3, ...`

For N user levels, use the first N weights and normalize them to that basket's budget. Hyperliquid order minimums and size/price precision are applied after normalization; preview must expose any adjustment.

## Exit v1

- Lower basket: reduce-only take-profit at GALKA.
- Upper basket: reduce-only take-profit from the weighted average entry of the upper basket only.
- Initial target for upper basket: +3.5% underlying price move from its weighted average entry (approximately +10.5% gross ROE at 3x before fees/funding).
- Lower fills must not alter upper-basket average or upper-basket TP.

## Liquidation safety gate

The campaign must fail closed if liquidation safety cannot be demonstrated.

Before allowing LIVE launch, preview must evaluate every sequential fill state from the first entry through the deepest planned lower entry.

Required invariant:

- estimated liquidation price must remain below the deepest planned lower limit,
- and must retain an additional 5% price buffer below that deepest lower limit.

For a deepest lower entry price `P_last`, the maximum acceptable liquidation price for a long campaign is:

`P_liq <= P_last * 0.95`

If this invariant fails at any sequential fill state, LIVE launch is blocked. The UI should show the failing fill state, estimated liquidation price, required threshold, and suggest reducing leverage and/or notional rather than silently changing user levels.

## Safety inheritance

Keep hardened GALKA LIVE protections: exchange truth/reconciliation, isolated-only, reduce-only exits, safe mode on inconsistencies, owned-order checks, atomic state, no silent success assumptions.

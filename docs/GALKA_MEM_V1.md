# GALKA MEM v1

Working rules agreed for the first live meme-perp campaign variant.

## Account and risk

- Uses the same Hyperliquid account/API wallet as hardened GALKA LIVE.
- Isolated margin only.
- Campaign risk/margin budget is user-selectable; current working ceiling/default for the first version is $100.
- Leverage is user-selectable up to the selected market's venue maximum.
- The application must not assume that wallet balance is the campaign risk budget.

## Manual structure

User enters:
- coin/perp,
- leverage,
- GALKA price,
- manual entry prices for the upper basket,
- campaign budget.

Upper-basket levels are chosen manually by the user. GALKA itself may be an upper-basket entry. Upper entries must be between GALKA and +5% from GALKA; typical intended entries are around +2%, +3%, +4% and/or GALKA.

The lower basket uses the first-version default ladder relative to GALKA:

`-2%, -4%, -6%, -8%`

These lower levels can become editable in a later version; for v1 they are the default automatic lower basket.

## Two independent baskets

Entries are managed as two logical baskets so fills below GALKA never move the take-profit calculation of the upper basket.

Default budget split:
- upper basket (GALKA and above): 1/3 of campaign margin,
- lower basket (below GALKA): 2/3 of campaign margin.

For a $100 campaign this is approximately:
- upper: $33.33 margin,
- lower: $66.67 margin.

The full budget assigned to each basket is distributed across the levels that exist in that basket. No unused reserve is kept inside the upper basket merely because the user selected fewer upper levels.

Within each basket, size increases as price gets lower. Weight sequence:

`1, 1.5, 2, 2.5, 3, ...`

For N levels in a basket, use the first N weights and normalize them to 100% of that basket's budget. Hyperliquid order minimums and size/price precision are applied after normalization; preview must expose any adjustment.

Example: with two upper levels, the upper $33.33 is normalized across weights `1, 1.5`, approximately $13.33 and $20.00 of margin before exchange rounding.

## Campaign lifecycle and exits v1

A GALKA MEM setup is traded only once. There is no re-arm and no second cycle on the same GALKA.

Two possible paths:

1. Small cycle: upper basket fills and later reaches its upper-basket take-profit before the campaign extends into the lower basket. As soon as that upper basket is fully closed in profit, the entire campaign is finished. All still-pending lower-basket entry orders for this GALKA are canceled. This GALKA is not traded again.

2. Large cycle: upper basket has started filling, but price continues down through GALKA and into the lower basket before the small-cycle exit completes. The campaign then continues as one large cycle using the lower basket. Lower-basket entries are managed toward the GALKA exit. Once the resulting campaign position is closed according to the large-cycle exit logic, the entire campaign is finished and this GALKA is not traded again.

Exit rules:
- Upper basket: reduce-only take-profit from the weighted average entry of the upper basket only.
- Initial upper target: +3.5% underlying price move from the weighted average upper entry (approximately +10.5% gross ROE at 3x before fees/funding).
- Lower basket: target is GALKA.
- Lower fills must not alter the stored upper-basket average used to define the upper basket's original small-cycle TP.
- If the small cycle completes first, cancel every remaining lower entry order immediately after exchange-confirmed closure.
- If price enters the lower basket before the small cycle completes, do not treat a later upper-only TP touch as a fresh small cycle; the campaign is already in the large-cycle path.
- After either path finishes, the campaign is permanently closed for that GALKA.

## Liquidation safety gate

The campaign must fail closed if liquidation safety cannot be demonstrated.

Before allowing LIVE launch, preview must evaluate every sequential fill state from the first upper entry through the deepest planned lower entry.

Required invariant:

- estimated liquidation price must remain below the deepest planned lower limit,
- and must retain an additional 5% price buffer below that deepest lower limit.

For a deepest lower entry price `P_last`, the maximum acceptable liquidation price for a long campaign is:

`P_liq <= P_last * 0.95`

If this invariant fails at any sequential fill state, LIVE launch is blocked. The UI should show the failing fill state, estimated liquidation price, required threshold, and suggest reducing leverage and/or notional rather than silently changing user levels.

## Safety inheritance

Keep hardened GALKA LIVE protections: exchange truth/reconciliation, isolated-only, reduce-only exits, safe mode on inconsistencies, owned-order checks, atomic state, no silent success assumptions.

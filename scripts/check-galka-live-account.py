#!/usr/bin/env python3
"""Read-only online check of the configured Hyperliquid account."""

from __future__ import annotations

from live.config import load_config
from live.hyperliquid_gateway import HyperliquidGateway


def main() -> int:
    config = load_config()
    if config.live_enabled:
        raise SystemExit(
            "Для read-only проверки сначала установи HL_LIVE_ENABLED=NO и перезапусти команду."
        )

    gateway = HyperliquidGateway(config)
    account = gateway.fresh_account_state()
    mids = gateway.mids()
    orders = gateway.fresh_open_orders()

    print(f"Сеть: {config.network_name}")
    print(f"Основной аккаунт: {config.masked_address}")
    print(f"API Wallet: {gateway.agent_address[:6]}…{gateway.agent_address[-4:]}")
    print(f"Account value: ${account['accountValue']:.2f}")
    print(f"Withdrawable: ${account['withdrawable']:.2f}")
    print(
        "Mids: "
        + " · ".join(f"{coin} {mids.get(coin, 0):g}" for coin in ("BTC", "ETH", "SOL"))
    )

    positions = account.get("positions", {})
    supported_positions = [
        (coin, row)
        for coin, row in positions.items()
        if coin in {"BTC", "ETH", "SOL"} and abs(float(row.get("size") or 0)) > 0
    ]
    if supported_positions:
        print("ВНИМАНИЕ: обнаружены позиции:")
        for coin, row in supported_positions:
            print(f"  {coin}: size={float(row['size']):g}, entry={float(row.get('entryPrice') or 0):g}")
    else:
        print("Позиции BTC/ETH/SOL: нет")

    supported_orders = [row for row in orders if row.get("coin") in {"BTC", "ETH", "SOL"}]
    print(f"Открытые ордера BTC/ETH/SOL: {len(supported_orders)}")
    for row in supported_orders[:20]:
        print(
            f"  {row['coin']} oid={row['oid']} side={row.get('side')} "
            f"size={row.get('size'):g} px={row.get('price'):g} reduceOnly={row.get('reduceOnly')}"
        )
    if len(supported_orders) > 20:
        print(f"  ... ещё {len(supported_orders) - 20}")

    print("READ-ONLY CHECK PASS: торговые команды не отправлялись.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import json
import math
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from hyperliquid.info import Info
from hyperliquid.utils import constants

from live.config import load_config


def finite(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def iso_to_ms(value: Any) -> int:
    text = str(value or "").strip()
    if not text:
        return 0
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return int(parsed.timestamp() * 1000)
    except ValueError:
        return 0


def fmt_time(ms: Any) -> str:
    value = as_int(ms)
    if value <= 0:
        return "—"
    return datetime.fromtimestamp(value / 1000).astimezone().strftime("%Y-%m-%d %H:%M:%S %z")


def fmt_money(value: Any) -> str:
    number = finite(value)
    return f"${number:+.4f}" if number else "$0.0000"


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SystemExit(f"Не удалось прочитать {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit(f"Некорректный JSON: {path}")
    return payload


def had_trade(campaign: dict[str, Any]) -> bool:
    if bool(campaign.get("hadPosition")) or as_int(campaign.get("cycleDeepest")) > 0:
        return True
    return any(finite(row.get("filledSize")) > 0 for row in campaign.get("levels", []))


def campaign_sort_key(campaign: dict[str, Any]) -> tuple[int, int]:
    return (
        iso_to_ms(campaign.get("completedAt") or campaign.get("updatedAt")),
        as_int(campaign.get("createdMs")),
    )


def weighted_price(rows: list[dict[str, Any]]) -> float:
    size = sum(abs(finite(row.get("sz"))) for row in rows)
    if size <= 0:
        return 0.0
    return sum(abs(finite(row.get("sz"))) * finite(row.get("px")) for row in rows) / size


def main() -> int:
    config_path = os.environ.get("GALKA_LIVE_CONFIG")
    if not config_path:
        candidates = [
            Path.home() / ".local/run/galka-v2/runtime.env",
            Path.home() / ".config/galka-v2-test.env",
            Path.home() / ".config/galka-live.env",
        ]
        config_path = str(next((p for p in candidates if p.is_file()), candidates[-1]))

    config = load_config(config_path)
    state_path = config.data_dir / "state.json"
    state = read_json(state_path)
    campaigns = [row for row in state.get("campaigns", {}).values() if isinstance(row, dict) and had_trade(row)]
    if not campaigns:
        raise SystemExit("В локальном state нет кампаний с исполненными входами.")
    campaigns.sort(key=campaign_sort_key, reverse=True)
    campaign = campaigns[0]

    campaign_id = str(campaign.get("id") or "")
    coin = str(campaign.get("coin") or "").upper()
    created_ms = as_int(campaign.get("createdMs"))
    completed_ms = iso_to_ms(campaign.get("completedAt"))
    end_ms = max(completed_ms + 15 * 60_000, int(time.time() * 1000)) if not completed_ms else completed_ms + 15 * 60_000
    start_ms = max(0, created_ms - 5 * 60_000)

    entry_oid_map = {as_int(k): as_int(v) for k, v in (campaign.get("entryOidMap") or {}).items() if as_int(k) > 0}
    target_oid_map = {as_int(k): as_int(v) for k, v in (campaign.get("targetOidMap") or {}).items() if as_int(k) > 0}
    fallback_oid = as_int(campaign.get("fallbackTargetOid"))
    if fallback_oid > 0:
        target_oid_map.setdefault(fallback_oid, 0)

    print("=== GALKA LAST CAMPAIGN — READ ONLY ===")
    print(f"coin: {coin}")
    print(f"campaign: {campaign_id}")
    print(f"created: {fmt_time(created_ms)}")
    print(f"completed: {campaign.get('completedAt') or '—'}")
    print(f"status: {campaign.get('status')}")
    print(f"GALKA/state price: {finite(campaign.get('galkaPrice')):.8f}")
    if campaign.get("manualExitPrice") is not None:
        print(f"manualExitPrice: {finite(campaign.get('manualExitPrice')):.8f}")
    print(f"leverage: {campaign.get('leverage')}x")
    print(f"requestedNotional: {finite(campaign.get('requestedNotional')):.4f}")
    print(f"actualNotional: {finite(campaign.get('actualNotional')):.4f}")
    print(f"cycleDeepest: L{as_int(campaign.get('cycleDeepest'))}")
    print(f"cycleClosedPnl(state): {fmt_money(campaign.get('cycleClosedPnl'))}")
    print(f"cycleFees(state): {fmt_money(campaign.get('cycleFees'))}")
    print(f"finalClosedPnl(state): {fmt_money(campaign.get('finalClosedPnl'))}")
    print("\nLEVEL STATE")
    for level in sorted(campaign.get("levels", []), key=lambda row: as_int(row.get("index"))):
        index = as_int(level.get("index"))
        print(
            f"L{index}: price={finite(level.get('price')):.8f} "
            f"status={level.get('status')} filled={finite(level.get('filledSize')):.10f} "
            f"avgFill={finite(level.get('averageFillPrice')):.8f} "
            f"notional={finite(level.get('notional')):.4f}"
        )

    base_url = constants.MAINNET_API_URL if config.mainnet else constants.TESTNET_API_URL
    try:
        info = Info(base_url, skip_ws=True, timeout=config.request_timeout)
        venue_rows = info.user_fills_by_time(
            config.account_address,
            start_ms,
            end_ms,
            aggregate_by_time=False,
        )
    except Exception as exc:
        print(f"\nEXCHANGE FILLS: не удалось прочитать ({type(exc).__name__}: {exc})")
        venue_rows = []

    coin_rows = [row for row in venue_rows if str(row.get("coin") or "").upper() == coin]
    matched: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    for row in coin_rows:
        oid = as_int(row.get("oid"))
        copy = dict(row)
        if oid in entry_oid_map:
            copy["_role"] = f"ENTRY L{entry_oid_map[oid]}"
            copy["_level"] = entry_oid_map[oid]
            matched.append(copy)
        elif oid in target_oid_map:
            level = target_oid_map[oid]
            copy["_role"] = f"TARGET L{level}" if level > 0 else "MANUAL/FALLBACK EXIT"
            copy["_level"] = level
            matched.append(copy)
        else:
            copy["_role"] = "UNMATCHED"
            unmatched.append(copy)

    matched.sort(key=lambda row: as_int(row.get("time")))
    unmatched.sort(key=lambda row: as_int(row.get("time")))

    print("\nEXCHANGE FILLS MATCHED TO THIS CAMPAIGN")
    if not matched:
        print("none matched by campaign OID")
    for row in matched:
        print(
            f"{fmt_time(row.get('time'))} | {row['_role']} | "
            f"{row.get('dir')} side={row.get('side')} px={finite(row.get('px')):.8f} "
            f"sz={finite(row.get('sz')):.10f} fee={fmt_money(row.get('fee'))} "
            f"closedPnl={fmt_money(row.get('closedPnl'))} crossed={row.get('crossed')} oid={row.get('oid')}"
        )

    entry_rows = [row for row in matched if str(row.get("_role", "")).startswith("ENTRY")]
    exit_rows = [row for row in matched if row not in entry_rows and str(row.get("side")) == "A"]
    filled_levels = sorted({as_int(row.get("_level")) for row in entry_rows if as_int(row.get("_level")) > 0})
    total_entry_size = sum(abs(finite(row.get("sz"))) for row in entry_rows)
    total_entry_notional = sum(abs(finite(row.get("sz"))) * finite(row.get("px")) for row in entry_rows)
    total_exit_size = sum(abs(finite(row.get("sz"))) for row in exit_rows)
    gross_closed = sum(finite(row.get("closedPnl")) for row in exit_rows)
    fees = sum(finite(row.get("fee")) for row in matched)
    net_trade = gross_closed - fees

    print("\nSUMMARY FROM EXCHANGE FILLS")
    print("filled levels: " + (", ".join(f"L{x}" for x in filled_levels) if filled_levels else "—"))
    print(f"entry fills: {len(entry_rows)}, exit fills: {len(exit_rows)}")
    print(f"entry size: {total_entry_size:.10f}, entry notional: ${total_entry_notional:.4f}")
    print(f"weighted entry: {weighted_price(entry_rows):.8f}")
    print(f"exit size: {total_exit_size:.10f}")
    print(f"weighted exit: {weighted_price(exit_rows):.8f}")
    print(f"gross closedPnl: {fmt_money(gross_closed)}")
    print(f"trade fees: {fmt_money(fees)}")
    print(f"net after trade fees (funding excluded): {fmt_money(net_trade)}")

    auto_targets = [row for row in exit_rows if str(row.get("_role", "")).startswith("TARGET L")]
    manual_targets = [row for row in exit_rows if row.get("_role") == "MANUAL/FALLBACK EXIT"]
    if auto_targets and not manual_targets:
        print("close classification: automatic owned GALKA target(s)")
    elif manual_targets:
        print("close classification: manual/near-market fallback exit participated")
    elif exit_rows:
        print("close classification: owned exit, type unclear")
    else:
        print("close classification: no owned exit fill matched")

    nearby_unmatched = [
        row for row in unmatched
        if start_ms <= as_int(row.get("time")) <= end_ms
    ]
    if nearby_unmatched:
        print("\nOTHER SAME-COIN FILLS IN THE SAME TIME WINDOW (not owned by stored campaign OIDs)")
        for row in nearby_unmatched[-20:]:
            print(
                f"{fmt_time(row.get('time'))} | {row.get('dir')} side={row.get('side')} "
                f"px={finite(row.get('px')):.8f} sz={finite(row.get('sz')):.10f} "
                f"fee={fmt_money(row.get('fee'))} closedPnl={fmt_money(row.get('closedPnl'))} "
                f"crossed={row.get('crossed')} oid={row.get('oid')}"
            )

    print("\nCAMPAIGN EVENTS")
    events = []
    for event in state.get("events", []):
        if not isinstance(event, dict):
            continue
        meta = event.get("meta") if isinstance(event.get("meta"), dict) else {}
        if str(meta.get("campaignId") or "") == campaign_id:
            events.append(event)
    for event in events[-30:]:
        print(f"{event.get('time')} | {event.get('type')} | {event.get('message')}")

    print("=== END READ-ONLY REPORT ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import math
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
SECRET_RE = re.compile(r"^0x[a-fA-F0-9]{64}$")


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class LiveConfig:
    account_address: str
    api_secret_key: str
    mainnet: bool
    live_enabled: bool
    leverage: int
    isolated: bool
    total_notional: float
    host: str
    port: int
    config_path: Path
    data_dir: Path
    request_timeout: float = 8.0
    max_margin_fraction: float = 0.60
    maker_fee_rate: float = 0.00015
    taker_fee_rate: float = 0.00045
    monitor_interval: float = 6.0
    global_check_interval: float = 30.0

    @property
    def network_name(self) -> str:
        return "mainnet" if self.mainnet else "testnet"

    @property
    def masked_address(self) -> str:
        return f"{self.account_address[:6]}…{self.account_address[-4:]}"


def _parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ConfigError(f"Invalid config line: {raw_line}")
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _finite_float(values: dict[str, str], key: str, default: str) -> float:
    try:
        value = float(values.get(key, default))
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{key} must be a number") from exc
    if not math.isfinite(value):
        raise ConfigError(f"{key} must be finite")
    return value


def _check_permissions(path: Path) -> None:
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}. Run bash scripts/setup-galka-live.sh")
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise ConfigError(
            f"Unsafe permissions on {path}: run chmod 600 {path} before starting live trading"
        )


def load_config(path: str | Path | None = None) -> LiveConfig:
    config_path = Path(
        path
        or os.environ.get("GALKA_LIVE_CONFIG")
        or Path.home() / ".config" / "galka-live.env"
    ).expanduser()
    _check_permissions(config_path)
    values = _parse_env(config_path)

    account_address = values.get("HL_ACCOUNT_ADDRESS", "").strip()
    api_secret_key = values.get("HL_API_SECRET_KEY", "").strip()
    if not ADDRESS_RE.fullmatch(account_address):
        raise ConfigError("HL_ACCOUNT_ADDRESS must be the 0x address of the main Hyperliquid account")
    if not SECRET_RE.fullmatch(api_secret_key):
        raise ConfigError("HL_API_SECRET_KEY must be the 0x private key of the approved API wallet")

    try:
        leverage = int(values.get("HL_LEVERAGE", "10"))
    except (TypeError, ValueError) as exc:
        raise ConfigError("HL_LEVERAGE must be an integer") from exc
    if leverage < 1 or leverage > 10:
        raise ConfigError("HL_LEVERAGE must be between 1 and 10")

    total_notional = _finite_float(values, "HL_TOTAL_NOTIONAL", "150")
    if total_notional < 80 or total_notional > 1000:
        raise ConfigError("HL_TOTAL_NOTIONAL must be between $80 and $1000")

    request_timeout = _finite_float(values, "HL_REQUEST_TIMEOUT", "8")
    if request_timeout < 2 or request_timeout > 30:
        raise ConfigError("HL_REQUEST_TIMEOUT must be between 2 and 30 seconds")

    max_margin_fraction = _finite_float(values, "HL_MAX_MARGIN_FRACTION", "0.60")
    if max_margin_fraction < 0.10 or max_margin_fraction > 0.90:
        raise ConfigError("HL_MAX_MARGIN_FRACTION must be between 0.10 and 0.90")

    maker_fee_rate = _finite_float(values, "HL_MAKER_FEE_RATE", "0.00015")
    taker_fee_rate = _finite_float(values, "HL_TAKER_FEE_RATE", "0.00045")
    if maker_fee_rate < 0 or maker_fee_rate > 0.01:
        raise ConfigError("HL_MAKER_FEE_RATE must be between 0 and 0.01")
    if taker_fee_rate < 0 or taker_fee_rate > 0.01:
        raise ConfigError("HL_TAKER_FEE_RATE must be between 0 and 0.01")

    monitor_interval = _finite_float(values, "HL_MONITOR_INTERVAL", "6")
    global_check_interval = _finite_float(values, "HL_GLOBAL_CHECK_INTERVAL", "30")
    if monitor_interval < 3 or monitor_interval > 30:
        raise ConfigError("HL_MONITOR_INTERVAL must be between 3 and 30 seconds")
    if global_check_interval < 10 or global_check_interval > 300:
        raise ConfigError("HL_GLOBAL_CHECK_INTERVAL must be between 10 and 300 seconds")

    isolated = _bool(values.get("HL_ISOLATED"), True)
    if not isolated:
        raise ConfigError("HL_ISOLATED must remain true for the hardened LIVE engine")

    live_enabled = (
        values.get("HL_LIVE_ENABLED", "NO").strip().upper() == "YES"
        and values.get("HL_LIVE_CONFIRM", "").strip() == "I_UNDERSTAND_REAL_MONEY"
    )
    host = values.get("GALKA_HOST", "127.0.0.1").strip()
    if host not in {"127.0.0.1", "localhost"}:
        raise ConfigError("GALKA_HOST must remain 127.0.0.1 or localhost")
    try:
        port = int(values.get("GALKA_PORT", "8098"))
    except (TypeError, ValueError) as exc:
        raise ConfigError("GALKA_PORT must be an integer") from exc
    if not 1024 <= port <= 65535:
        raise ConfigError("GALKA_PORT is invalid")

    data_dir = Path(
        values.get("GALKA_DATA_DIR", str(Path.home() / ".local" / "share" / "galka-live"))
    ).expanduser()
    data_dir.mkdir(parents=True, exist_ok=True)
    try:
        data_dir.chmod(0o700)
    except OSError:
        pass

    return LiveConfig(
        account_address=account_address.lower(),
        api_secret_key=api_secret_key,
        mainnet=_bool(values.get("HL_MAINNET"), True),
        live_enabled=live_enabled,
        leverage=leverage,
        isolated=isolated,
        total_notional=total_notional,
        host="127.0.0.1",
        port=port,
        config_path=config_path,
        data_dir=data_dir,
        request_timeout=request_timeout,
        max_margin_fraction=max_margin_fraction,
        maker_fee_rate=maker_fee_rate,
        taker_fee_rate=taker_fee_rate,
        monitor_interval=monitor_interval,
        global_check_interval=global_check_interval,
    )

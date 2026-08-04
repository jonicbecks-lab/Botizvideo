from __future__ import annotations

import math
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
SECRET_RE = re.compile(r"^0x[a-fA-F0-9]{64}$")
REPO_ROOT = Path(__file__).resolve().parents[1]
MAX_CONFIG_BYTES = 64_000
ALLOWED_KEYS = {
    "HL_ACCOUNT_ADDRESS",
    "HL_API_SECRET_KEY",
    "HL_MAINNET",
    "HL_LIVE_ENABLED",
    "HL_LIVE_CONFIRM",
    "HL_LEVERAGE",
    "HL_ISOLATED",
    "HL_TOTAL_NOTIONAL",
    "HL_REQUEST_TIMEOUT",
    "HL_MAX_MARGIN_FRACTION",
    "HL_MAKER_FEE_RATE",
    "HL_TAKER_FEE_RATE",
    "HL_MONITOR_INTERVAL",
    "HL_GLOBAL_CHECK_INTERVAL",
    "GALKA_HOST",
    "GALKA_PORT",
    "GALKA_DATA_DIR",
}


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
    max_margin_fraction: float = 0.95
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


def _is_inside_repo(path: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(REPO_ROOT)
    except ValueError:
        return False
    return True


def _read_private_config(path: Path) -> str:
    if _is_inside_repo(path):
        raise ConfigError("The LIVE config must remain outside the Git repository")
    if path.is_symlink():
        raise ConfigError("The LIVE config must be a regular file, not a symlink")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError as exc:
        raise ConfigError(f"Config file not found: {path}. Run bash scripts/setup-galka-live.sh") from exc
    except OSError as exc:
        raise ConfigError(f"Cannot securely open LIVE config: {path}") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ConfigError("The LIVE config must be a regular file")
        mode = stat.S_IMODE(metadata.st_mode)
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise ConfigError(
                f"Unsafe permissions on {path}: run chmod 600 {path} before starting live trading"
            )
        if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
            raise ConfigError("The LIVE config must be owned by the current user")
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            descriptor = -1
            contents = handle.read(MAX_CONFIG_BYTES + 1)
        if len(contents.encode("utf-8")) > MAX_CONFIG_BYTES:
            raise ConfigError("The LIVE config is unexpectedly large")
        return contents
    except UnicodeDecodeError as exc:
        raise ConfigError("The LIVE config must be valid UTF-8") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _parse_env(contents: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in contents.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ConfigError(f"Invalid config line: {raw_line}")
        key, value = line.split("=", 1)
        key = key.strip()
        if key in values:
            raise ConfigError(f"Duplicate config key: {key}")
        if key not in ALLOWED_KEYS:
            raise ConfigError(f"Unknown config key: {key}")
        values[key] = value.strip().strip('"').strip("'")
    return values


def _bool(values: dict[str, str], key: str, default: bool = False) -> bool:
    value = values.get(key)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{key} must be true or false")


def _finite_float(values: dict[str, str], key: str, default: str) -> float:
    try:
        value = float(values.get(key, default))
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{key} must be a number") from exc
    if not math.isfinite(value):
        raise ConfigError(f"{key} must be finite")
    return value


def load_config(path: str | Path | None = None) -> LiveConfig:
    config_path = Path(
        path
        or os.environ.get("GALKA_LIVE_CONFIG")
        or Path.home() / ".config" / "galka-live.env"
    ).expanduser().absolute()
    values = _parse_env(_read_private_config(config_path))

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

    total_notional = _finite_float(values, "HL_TOTAL_NOTIONAL", "2000")
    if total_notional < 80 or total_notional > 5000:
        raise ConfigError("HL_TOTAL_NOTIONAL must be between $80 and $5000")

    request_timeout = _finite_float(values, "HL_REQUEST_TIMEOUT", "8")
    if request_timeout < 2 or request_timeout > 30:
        raise ConfigError("HL_REQUEST_TIMEOUT must be between 2 and 30 seconds")

    max_margin_fraction = _finite_float(values, "HL_MAX_MARGIN_FRACTION", "0.95")
    if max_margin_fraction < 0.10 or max_margin_fraction > 0.97:
        raise ConfigError("HL_MAX_MARGIN_FRACTION must be between 0.10 and 0.97")

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

    isolated = _bool(values, "HL_ISOLATED", True)
    if not isolated:
        raise ConfigError("HL_ISOLATED must remain true for the hardened LIVE engine")

    live_flag = values.get("HL_LIVE_ENABLED", "NO").strip().upper()
    if live_flag not in {"YES", "NO"}:
        raise ConfigError("HL_LIVE_ENABLED must be YES or NO")
    live_enabled = (
        live_flag == "YES"
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
    ).expanduser().absolute()
    if _is_inside_repo(data_dir):
        raise ConfigError("GALKA_DATA_DIR must remain outside the Git repository")
    if data_dir.is_symlink():
        raise ConfigError("GALKA_DATA_DIR must not be a symlink")
    data_dir.mkdir(parents=True, exist_ok=True)
    if not data_dir.is_dir():
        raise ConfigError("GALKA_DATA_DIR must be a directory")
    try:
        data_dir.chmod(0o700)
    except OSError:
        pass

    return LiveConfig(
        account_address=account_address.lower(),
        api_secret_key=api_secret_key,
        mainnet=_bool(values, "HL_MAINNET", True),
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

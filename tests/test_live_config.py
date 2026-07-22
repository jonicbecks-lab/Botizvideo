from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from live.config import ConfigError, load_config


BASE = """\
HL_ACCOUNT_ADDRESS=0x1111111111111111111111111111111111111111
HL_API_SECRET_KEY=0x3333333333333333333333333333333333333333333333333333333333333333
HL_MAINNET=true
HL_LEVERAGE=10
HL_ISOLATED=true
HL_TOTAL_NOTIONAL=200
HL_LIVE_ENABLED=NO
HL_LIVE_CONFIRM=NOT_CONFIRMED
GALKA_HOST=127.0.0.1
GALKA_PORT=8098
"""


class LiveConfigTests(unittest.TestCase):
    def write_config(self, extra=""):
        root = tempfile.TemporaryDirectory()
        path = Path(root.name) / "galka-live.env"
        data_dir = Path(root.name) / "data"
        path.write_text(BASE + f"GALKA_DATA_DIR={data_dir}\n" + extra, encoding="utf-8")
        path.chmod(0o600)
        self.addCleanup(root.cleanup)
        return path

    def test_defaults_include_bounded_risk_and_fee_estimates(self):
        config = load_config(self.write_config())
        self.assertEqual(config.request_timeout, 8.0)
        self.assertEqual(config.max_margin_fraction, 0.60)
        self.assertEqual(config.maker_fee_rate, 0.00015)
        self.assertEqual(config.taker_fee_rate, 0.00045)
        self.assertEqual(config.monitor_interval, 6.0)
        self.assertEqual(config.global_check_interval, 30.0)

    def test_nan_and_infinity_are_rejected(self):
        for key, value in [
            ("HL_TOTAL_NOTIONAL", "nan"),
            ("HL_REQUEST_TIMEOUT", "inf"),
            ("HL_MAX_MARGIN_FRACTION", "-inf"),
            ("HL_MAKER_FEE_RATE", "nan"),
            ("HL_MONITOR_INTERVAL", "inf"),
        ]:
            path = self.write_config(f"\n{key}={value}\n")
            with self.subTest(key=key), self.assertRaises(ConfigError):
                load_config(path)

    def test_unsafe_permissions_are_rejected(self):
        path = self.write_config()
        path.chmod(0o644)
        with self.assertRaisesRegex(ConfigError, "Unsafe permissions"):
            load_config(path)

    def test_notional_cap_is_1000(self):
        config = load_config(self.write_config("\nHL_TOTAL_NOTIONAL=1000\n"))
        self.assertEqual(config.total_notional, 1000)
        with self.assertRaises(ConfigError):
            load_config(self.write_config("\nHL_TOTAL_NOTIONAL=1000.01\n"))

    def test_cross_margin_is_rejected(self):
        with self.assertRaisesRegex(ConfigError, "HL_ISOLATED"):
            load_config(self.write_config("\nHL_ISOLATED=false\n"))


if __name__ == "__main__":
    unittest.main()

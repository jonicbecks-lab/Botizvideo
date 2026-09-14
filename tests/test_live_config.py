from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from live.config import REPO_ROOT, ConfigError, load_config


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
    def write_config(self, overrides=None, extra_lines="", parent=None):
        root = tempfile.TemporaryDirectory(dir=parent)
        path = Path(root.name) / "galka-live.env"
        data_dir = Path(root.name) / "data"
        values = {}
        for line in BASE.splitlines():
            if line:
                key, value = line.split("=", 1)
                values[key] = value
        values["GALKA_DATA_DIR"] = str(data_dir)
        values.update(overrides or {})
        contents = "".join(f"{key}={value}\n" for key, value in values.items()) + extra_lines
        path.write_text(contents, encoding="utf-8")
        path.chmod(0o600)
        self.addCleanup(root.cleanup)
        return path

    def test_defaults_include_bounded_risk_and_fee_estimates(self):
        config = load_config(self.write_config())
        self.assertEqual(config.request_timeout, 8.0)
        self.assertEqual(config.max_margin_fraction, 0.95)
        self.assertEqual(config.maker_fee_rate, 0.00015)
        self.assertEqual(config.taker_fee_rate, 0.00045)
        self.assertEqual(config.monitor_interval, 6.0)
        self.assertEqual(config.global_check_interval, 30.0)

    def test_research_recorder_defaults_are_opt_in(self):
        config = load_config(self.write_config())
        self.assertFalse(config.research_recorder_enabled)
        self.assertEqual(config.research_l2_depth, 20)
        self.assertEqual(config.research_windows_ms, (100, 250, 500, 1000, 5000, 10000))
        self.assertEqual(config.research_feature_interval_ms, 100)
        self.assertEqual(config.research_book_bps, (1.0, 2.5, 5.0, 10.0, 25.0))
        self.assertEqual(config.research_large_trade_quantile, 0.95)
        self.assertTrue(str(config.research_recorder_dir).endswith("research/galka_campaigns"))

    def test_research_recorder_parameters_are_validated(self):
        config = load_config(
            self.write_config(
                {
                    "GALKA_RESEARCH_RECORDER_ENABLED": "true",
                    "GALKA_RESEARCH_L2_DEPTH": "12",
                    "GALKA_RESEARCH_WINDOWS_MS": "100,500,1000",
                    "GALKA_RESEARCH_BOOK_BPS": "1,5,10",
                    "GALKA_RESEARCH_IMBALANCE_RATIO": "2.5",
                    "GALKA_RESEARCH_STACKED_LEVELS": "4",
                }
            )
        )
        self.assertTrue(config.research_recorder_enabled)
        self.assertEqual(config.research_l2_depth, 12)
        self.assertEqual(config.research_windows_ms, (100, 500, 1000))
        self.assertEqual(config.research_book_bps, (1.0, 5.0, 10.0))
        self.assertEqual(config.research_imbalance_ratio, 2.5)
        self.assertEqual(config.research_stacked_levels, 4)

        for key, value in [
            ("GALKA_RESEARCH_L2_DEPTH", "21"),
            ("GALKA_RESEARCH_WINDOWS_MS", "0,100"),
            ("GALKA_RESEARCH_LARGE_TRADE_QUANTILE", "1"),
            ("GALKA_RESEARCH_QUEUE_MAX", "50"),
        ]:
            with self.subTest(key=key), self.assertRaises(ConfigError):
                load_config(self.write_config({key: value}))

    def test_nan_and_infinity_are_rejected(self):
        for key, value in [
            ("HL_TOTAL_NOTIONAL", "nan"),
            ("HL_REQUEST_TIMEOUT", "inf"),
            ("HL_MAX_MARGIN_FRACTION", "-inf"),
            ("HL_MAKER_FEE_RATE", "nan"),
            ("HL_MONITOR_INTERVAL", "inf"),
            ("GALKA_RESEARCH_IMBALANCE_RATIO", "nan"),
        ]:
            path = self.write_config({key: value})
            with self.subTest(key=key), self.assertRaises(ConfigError):
                load_config(path)

    def test_unsafe_permissions_are_rejected(self):
        path = self.write_config()
        path.chmod(0o644)
        with self.assertRaisesRegex(ConfigError, "Unsafe permissions"):
            load_config(path)

    def test_notional_cap_is_5000(self):
        config = load_config(self.write_config({"HL_TOTAL_NOTIONAL": "5000"}))
        self.assertEqual(config.total_notional, 5000)
        with self.assertRaises(ConfigError):
            load_config(self.write_config({"HL_TOTAL_NOTIONAL": "5000.01"}))

    def test_cross_margin_is_rejected(self):
        with self.assertRaisesRegex(ConfigError, "HL_ISOLATED"):
            load_config(self.write_config({"HL_ISOLATED": "false"}))

    def test_duplicate_and_unknown_keys_are_rejected(self):
        with self.assertRaisesRegex(ConfigError, "Duplicate config key"):
            load_config(self.write_config(extra_lines="HL_LEVERAGE=9\n"))
        with self.assertRaisesRegex(ConfigError, "Unknown config key"):
            load_config(self.write_config(extra_lines="HL_LIVE_ENABELD=NO\n"))

    def test_boolean_values_are_strict(self):
        with self.assertRaisesRegex(ConfigError, "HL_MAINNET"):
            load_config(self.write_config({"HL_MAINNET": "treu"}))
        with self.assertRaisesRegex(ConfigError, "HL_LIVE_ENABLED"):
            load_config(self.write_config({"HL_LIVE_ENABLED": "MAYBE"}))
        with self.assertRaisesRegex(ConfigError, "GALKA_RESEARCH_RECORDER_ENABLED"):
            load_config(self.write_config({"GALKA_RESEARCH_RECORDER_ENABLED": "maybe"}))

    def test_config_and_data_must_remain_outside_repository(self):
        repo_config = self.write_config(parent=REPO_ROOT)
        with self.assertRaisesRegex(ConfigError, "outside the Git repository"):
            load_config(repo_config)

        repo_data = REPO_ROOT / f".galka-live-test-{uuid4().hex}"
        with self.assertRaisesRegex(ConfigError, "outside the Git repository"):
            load_config(self.write_config({"GALKA_DATA_DIR": str(repo_data)}))
        self.assertFalse(repo_data.exists())

        repo_research = REPO_ROOT / f".galka-research-test-{uuid4().hex}"
        with self.assertRaisesRegex(ConfigError, "outside the Git repository"):
            load_config(
                self.write_config(
                    {
                        "GALKA_RESEARCH_RECORDER_ENABLED": "true",
                        "GALKA_RESEARCH_RECORDER_DIR": str(repo_research),
                    }
                )
            )
        self.assertFalse(repo_research.exists())

    def test_config_and_data_symlinks_are_rejected(self):
        target = self.write_config()
        link = target.parent / "linked.env"
        link.symlink_to(target)
        with self.assertRaisesRegex(ConfigError, "not a symlink"):
            load_config(link)

        real_data = target.parent / "real-data"
        real_data.mkdir()
        linked_data = target.parent / "linked-data"
        linked_data.symlink_to(real_data, target_is_directory=True)
        with self.assertRaisesRegex(ConfigError, "must not be a symlink"):
            load_config(self.write_config({"GALKA_DATA_DIR": str(linked_data)}))


if __name__ == "__main__":
    unittest.main()

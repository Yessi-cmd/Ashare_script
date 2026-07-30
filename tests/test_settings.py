import tempfile
import unittest
from pathlib import Path

from settings import ConfigError, get_owner_user_id, load_config, validate_config


class SettingsTests(unittest.TestCase):
    def test_owner_user_id_is_normalized(self):
        config = {"app": {"owner_user_id": "123"}}
        self.assertEqual(get_owner_user_id(config), 123)

    def test_invalid_signal_thresholds_are_rejected(self):
        with self.assertRaises(ConfigError):
            validate_config({"signal": {"buy_threshold": 20, "sell_threshold": 30}})

    def test_yaml_is_loaded_and_validated(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text(
                "monitor:\n  interval_seconds: 30\n"
                "signal:\n  buy_threshold: 70\n  sell_threshold: 30\n",
                encoding="utf-8",
            )
            config = load_config(path)
            self.assertEqual(config["monitor"]["interval_seconds"], 30)

    def test_global_market_configuration_is_validated(self):
        config = {
            "global_markets": {
                "poll_interval_seconds": 300,
                "request_timeout_seconds": 10,
                "markets": {
                    "us": {
                        "indices": [
                            {"symbol": "^GSPC", "name": "标普 500"},
                            {"symbol": "^IXIC", "name": "纳斯达克综合"},
                        ]
                    }
                },
            }
        }
        self.assertIs(validate_config(config), config)

    def test_unknown_global_market_is_rejected(self):
        with self.assertRaises(ConfigError):
            validate_config({"global_markets": {"markets": {"xx": {}}}})

    def test_global_markets_enabled_must_be_boolean(self):
        with self.assertRaises(ConfigError):
            validate_config({"global_markets": {"enabled": "false"}})


if __name__ == "__main__":
    unittest.main()

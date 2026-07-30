import unittest
from unittest.mock import patch

from research_universe import RESEARCH_UNIVERSE
from sync_universe import universe_codes


class SyncUniverseTests(unittest.TestCase):
    def test_yaml_universe_is_deduplicated(self):
        config = {
            "portfolio": {"000001": {}},
            "watchlist": {"000001": "平安银行", "600519": "贵州茅台"},
        }
        self.assertEqual(
            universe_codes(config),
            sorted(set(RESEARCH_UNIVERSE) | {"000001", "600519"}),
        )

    def test_owner_universe_comes_from_database_layer(self):
        config = {"app": {"owner_user_id": 123}}
        personal = {
            "portfolio": {"300750": {}},
            "watchlist": {"600036": "招商银行"},
        }
        with patch("sync_universe.load_user_config", return_value=personal):
            self.assertEqual(
                universe_codes(config),
                sorted(set(RESEARCH_UNIVERSE) | {"300750", "600036"}),
            )


if __name__ == "__main__":
    unittest.main()

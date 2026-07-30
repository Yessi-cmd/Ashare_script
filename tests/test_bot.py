import unittest
from unittest.mock import patch

import bot


class BotTests(unittest.TestCase):
    def test_stock_code_is_normalized(self):
        self.assertEqual(bot.normalize_stock_code("1"), "000001")

    def test_invalid_stock_code_is_rejected(self):
        with self.assertRaises(ValueError):
            bot.normalize_stock_code("ABC")

    def test_only_owner_is_allowed(self):
        with patch.object(bot, "OWNER_USER_ID", 123):
            self.assertTrue(bot.check_permission(123))
            self.assertFalse(bot.check_permission(456))


if __name__ == "__main__":
    unittest.main()

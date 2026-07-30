import unittest
from datetime import date

from holidays import get_next_trading_day, is_calendar_supported, is_trading_day


class HolidayTests(unittest.TestCase):
    def test_known_weekday_is_trading_day(self):
        self.assertTrue(is_trading_day(date(2026, 7, 17)))

    def test_unknown_year_fails_closed(self):
        self.assertFalse(is_calendar_supported(2027))
        self.assertFalse(is_trading_day(date(2027, 10, 1)))

    def test_next_day_does_not_cross_unsupported_calendar(self):
        with self.assertRaisesRegex(ValueError, "2027"):
            get_next_trading_day(date(2026, 12, 31))


if __name__ == "__main__":
    unittest.main()

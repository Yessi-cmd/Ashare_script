import unittest

import pandas as pd

from news import _format_us_indices


class NewsTests(unittest.TestCase):
    def test_change_uses_same_rows_previous_close(self):
        frame = pd.DataFrame(
            [
                {"name": "指数A", "close": 110, "preclose": 100},
                {"name": "指数B", "close": 210, "preclose": 200},
            ]
        )
        lines, has_change = _format_us_indices(frame)
        self.assertTrue(has_change)
        self.assertIn("+10.00%", lines[0])
        self.assertIn("+5.00%", lines[1])

    def test_missing_previous_close_is_not_fabricated(self):
        frame = pd.DataFrame([{"name": "指数A", "close": 110}])
        lines, has_change = _format_us_indices(frame)
        self.assertFalse(has_change)
        self.assertIn("涨跌幅暂无", lines[0])


if __name__ == "__main__":
    unittest.main()

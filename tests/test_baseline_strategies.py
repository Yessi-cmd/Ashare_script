import unittest

from baseline_strategies import score_ma_trend
from test_strategy_v3 import make_bars


class BaselineStrategyTests(unittest.TestCase):
    def test_ma_trend_distinguishes_uptrend_and_downtrend(self):
        up_score, _ = score_ma_trend(make_bars(60), 0, 0)
        down_score, _ = score_ma_trend(make_bars(60, direction=-1.0), 0, 0)

        self.assertEqual(up_score, 80)
        self.assertEqual(down_score, 50)


if __name__ == "__main__":
    unittest.main()

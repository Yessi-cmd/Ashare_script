import unittest

from strategy_comparison import compare_strategies
from test_backtest_engine import synthetic_bars


def one_signal_scorer(history, _price, _change):
    return (80, "test") if float(history.iloc[-1]["收盘"]) == 60.0 else (50, "test")


class StrategyComparisonTests(unittest.TestCase):
    def test_comparison_aggregates_multiple_stocks(self):
        bars = synthetic_bars(100)
        result = compare_strategies(
            {"000001": bars, "600000": bars},
            {"v2": one_signal_scorer, "v3": one_signal_scorer},
            horizon=5,
            commission_rate=0,
            stamp_duty_rate=0,
            slippage_rate=0,
        )
        self.assertEqual(result.aggregates["v2"].stock_count, 2)
        self.assertEqual(result.aggregates["v2"].summary.trade_count, 2)
        self.assertEqual(result.aggregates["v2"].summary, result.aggregates["v3"].summary)


if __name__ == "__main__":
    unittest.main()

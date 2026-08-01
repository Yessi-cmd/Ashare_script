import unittest
from datetime import date, timedelta

import pandas as pd

from portfolio_backtest import PortfolioConfig, run_portfolio_backtest


def make_bars(count=180, direction=1.0, code="000001"):
    start = date(2025, 1, 1)
    rows = []
    for index in range(count):
        close = 100 + direction * index * 0.35
        rows.append({
            "stock_code": code,
            "trade_date": start + timedelta(days=index),
            "open": close - direction * 0.1,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": 1_000_000 + index * 1_000,
            "amount": 100_000_000 + index * 100_000,
        })
    return pd.DataFrame(rows)


class PortfolioBacktestTests(unittest.TestCase):
    def base_config(self, **overrides):
        values = {
            "stock_codes": ("000001", "600000"),
            "lookback_bars": 126,
            "top_n": 1,
            "rebalance_every": 5,
            "min_amount": 0,
            "commission_rate": 0,
            "stamp_duty_rate": 0,
            "slippage_rate": 0,
        }
        values.update(overrides)
        return PortfolioConfig(**values)

    def test_rebalance_enters_next_day_and_respects_top_n(self):
        bars = {
            "000001": make_bars(direction=1.0, code="000001"),
            "600000": make_bars(direction=-1.0, code="600000"),
        }
        result = run_portfolio_backtest(bars, self.base_config())

        self.assertGreater(result.summary.rebalance_count, 0)
        self.assertTrue(result.rebalances[0].entry_date > result.rebalances[0].signal_date)
        self.assertTrue(all(len(row["holdings"]) <= 1 for row in result.equity_curve))
        self.assertEqual(result.summary.start_date, result.rebalances[0].entry_date)

    def test_transaction_cost_reduces_result(self):
        bars = {
            "000001": make_bars(direction=1.0, code="000001"),
            "600000": make_bars(direction=-1.0, code="600000"),
        }
        free = run_portfolio_backtest(bars, self.base_config())
        costly = run_portfolio_backtest(
            bars,
            self.base_config(
                commission_rate=0.001,
                stamp_duty_rate=0.001,
                slippage_rate=0.001,
            ),
        )

        self.assertGreater(costly.summary.turnover_pct, 0)
        self.assertLess(costly.summary.total_return_pct, free.summary.total_return_pct)

    def test_evaluation_end_excludes_future_rows(self):
        bars = {
            "000001": make_bars(direction=1.0, code="000001"),
            "600000": make_bars(direction=-1.0, code="600000"),
        }
        end = date(2025, 6, 20)
        baseline = run_portfolio_backtest(bars, self.base_config(), evaluation_end=end)
        mutated = {code: frame.copy() for code, frame in bars.items()}
        for frame in mutated.values():
            frame.loc[frame["trade_date"] > end, "close"] = 10_000
        changed = run_portfolio_backtest(mutated, self.base_config(), evaluation_end=end)

        self.assertEqual(baseline.summary, changed.summary)
        self.assertEqual(baseline.equity_curve, changed.equity_curve)


if __name__ == "__main__":
    unittest.main()

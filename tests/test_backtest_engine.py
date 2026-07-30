import unittest
from datetime import date, timedelta

import pandas as pd

from backtest_engine import (
    BacktestConfig,
    BacktestTrade,
    _summarize,
    calculate_score_lookup,
    run_backtest,
)


def synthetic_bars(count=40):
    start = date(2026, 1, 1)
    return pd.DataFrame([
        {
            "trade_date": start + timedelta(days=index),
            "open": float(index + 1),
            "high": float(index + 1.5),
            "low": float(index + 0.5),
            "close": float(index + 1),
            "volume": 1000.0,
        }
        for index in range(count)
    ])


class BacktestEngineTests(unittest.TestCase):
    def test_signal_enters_on_next_bar(self):
        seen_last_closes = []

        def scorer(history, _price, _change):
            last_close = float(history.iloc[-1]["收盘"])
            seen_last_closes.append(last_close)
            return (80, "test") if last_close == 20.0 else (50, "test")

        config = BacktestConfig(
            stock_code="000001",
            strategy_name="v2",
            horizons=(1,),
            warmup_bars=20,
            commission_rate=0,
            stamp_duty_rate=0,
            slippage_rate=0,
        )
        result = run_backtest(synthetic_bars(), config, scorer=scorer)
        trade = result.trades[1][0]
        self.assertEqual(trade.signal_date, date(2026, 1, 20))
        self.assertEqual(trade.entry_date, date(2026, 1, 21))
        self.assertEqual(seen_last_closes[0], 20.0)

    def test_summary_calculates_compounding_and_drawdown(self):
        trades = [
            BacktestTrade(date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3),
                          80, 10, 11, 10, 10),
            BacktestTrade(date(2026, 1, 4), date(2026, 1, 5), date(2026, 1, 6),
                          80, 10, 8, -20, -20),
        ]
        summary = _summarize(1, trades)
        self.assertAlmostEqual(summary.compounded_return_pct, -12.0)
        self.assertAlmostEqual(summary.max_drawdown_pct, -20.0)
        self.assertAlmostEqual(summary.profit_factor, 0.5)

    def test_evaluation_window_uses_prior_bars_only_as_warmup(self):
        def scorer(history, _price, _change):
            close = float(history.iloc[-1]["收盘"])
            return (80, "test") if close in {20.0, 31.0} else (50, "test")

        config = BacktestConfig(
            stock_code="000001",
            strategy_name="v2",
            horizons=(1,),
            warmup_bars=20,
            commission_rate=0,
            stamp_duty_rate=0,
            slippage_rate=0,
        )
        result = run_backtest(
            synthetic_bars(50),
            config,
            scorer=scorer,
            evaluation_start=date(2026, 1, 25),
            evaluation_end=date(2026, 2, 15),
        )

        self.assertEqual(result.signal_count, 1)
        self.assertEqual(result.trades[1][0].signal_date, date(2026, 1, 31))
        self.assertEqual(result.start_date, date(2026, 1, 25))

    def test_evaluation_window_requires_warmup_history(self):
        config = BacktestConfig(
            stock_code="000001",
            horizons=(1,),
            warmup_bars=20,
        )
        with self.assertRaisesRegex(ValueError, "预热日线"):
            run_backtest(
                synthetic_bars(40),
                config,
                evaluation_start=date(2026, 1, 10),
            )

    def test_precomputed_score_trace_is_exactly_equivalent(self):
        def scorer(history, _price, _change):
            close = float(history.iloc[-1]["收盘"])
            return (80, "test") if close in {20.0, 30.0} else (50, "test")

        bars = synthetic_bars(50)
        config = BacktestConfig(
            stock_code="000001",
            horizons=(5,),
            warmup_bars=20,
            commission_rate=0,
            stamp_duty_rate=0,
            slippage_rate=0,
        )
        baseline = run_backtest(bars, config, scorer=scorer)
        lookup = calculate_score_lookup(bars, config.warmup_bars, scorer)
        optimized = run_backtest(
            bars,
            config,
            scorer=scorer,
            score_lookup=lookup,
        )

        self.assertEqual(optimized.summaries, baseline.summaries)
        self.assertEqual(optimized.trades, baseline.trades)


if __name__ == "__main__":
    unittest.main()

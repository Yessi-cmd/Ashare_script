import unittest
from datetime import date, timedelta

import pandas as pd

from strategy_v3 import (
    evaluate_strategy_v3,
    make_strategy_v3_scorer,
    score_strategy_v3,
)


def make_bars(count=120, direction=1.0):
    start = date(2025, 1, 1)
    rows = []
    for index in range(count):
        close = 100 + direction * index * 0.3
        rows.append({
            "trade_date": start + timedelta(days=index),
            "open": close - direction * 0.1,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": 1000 + index * (5 if direction > 0 else 1),
        })
    return pd.DataFrame(rows)


class StrategyV3Tests(unittest.TestCase):
    def test_evaluation_is_explainable_and_bounded(self):
        bars = make_bars()
        evaluation = evaluate_strategy_v3(bars, market_bars=bars)
        self.assertGreaterEqual(evaluation.score, 0)
        self.assertLessEqual(evaluation.score, 100)
        self.assertEqual(len(evaluation.factors), 6)
        self.assertEqual(sum(factor.max_score for factor in evaluation.factors), 100)
        self.assertLess(evaluation.risk.stop_loss_pct, 0)
        self.assertGreater(evaluation.risk.take_profit_pct, 0)
        self.assertEqual(evaluation.confidence, "高")

    def test_bearish_market_scores_below_uptrend(self):
        uptrend = evaluate_strategy_v3(make_bars(), market_bars=make_bars())
        downtrend = evaluate_strategy_v3(
            make_bars(direction=-1.0),
            market_bars=make_bars(direction=-1.0),
        )
        self.assertGreater(uptrend.score, downtrend.score)

    def test_backtest_adapter_returns_reason(self):
        score, reason = score_strategy_v3(make_bars(60), 0, 0)
        self.assertIsInstance(score, int)
        self.assertIn(":", reason)

    def test_market_scorer_cannot_see_future_market_bars(self):
        stock = make_bars(60)
        market = make_bars(120, direction=-1.0)
        future_mask = market["trade_date"] > stock.iloc[-1]["trade_date"]
        market.loc[future_mask, "close"] = 1000.0
        market.loc[future_mask, "open"] = 999.0
        market.loc[future_mask, "high"] = 1001.0
        market.loc[future_mask, "low"] = 998.0

        expected = evaluate_strategy_v3(stock, market_bars=market.iloc[:60]).score
        score, _reason = make_strategy_v3_scorer(market)(stock, 0, 0)

        self.assertEqual(score, expected)


if __name__ == "__main__":
    unittest.main()

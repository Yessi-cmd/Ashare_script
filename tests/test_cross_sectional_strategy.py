import unittest
from datetime import date, timedelta

import pandas as pd

from cross_sectional_strategy import (
    SelectionConfig,
    compute_raw_factors,
    rank_universe_on_date,
)


def make_bars(count=160, direction=1.0, code="000001"):
    start = date(2025, 1, 1)
    rows = []
    for index in range(count):
        close = 100 + direction * index * 0.4
        rows.append({
            "stock_code": code,
            "trade_date": start + timedelta(days=index),
            "open": close - direction * 0.1,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": 1_000_000 + max(index, 0) * 1_000,
            "amount": 100_000_000 + max(index, 0) * 100_000,
        })
    return pd.DataFrame(rows)


class CrossSectionalStrategyTests(unittest.TestCase):
    def setUp(self):
        self.config = SelectionConfig(
            lookback_bars=126,
            top_n=1,
            min_amount=0,
        )

    def test_rank_prefers_trending_stock_on_same_date(self):
        up = make_bars(direction=1.0, code="000001")
        down = make_bars(direction=-1.0, code="600000")
        signal_date = up.iloc[-1]["trade_date"]

        result = rank_universe_on_date(
            {"000001": up, "600000": down},
            signal_date,
            self.config,
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].stock_code, "000001")
        self.assertEqual(result[0].rank, 1)
        self.assertGreaterEqual(result[0].score, 0)
        self.assertLessEqual(result[0].score, 100)

    def test_future_rows_do_not_change_signal_date_score(self):
        up = make_bars(direction=1.0, code="000001")
        down = make_bars(direction=-1.0, code="600000")
        signal_date = up.iloc[130]["trade_date"]
        baseline = rank_universe_on_date(
            {"000001": up, "600000": down}, signal_date, self.config
        )

        mutated = up.copy()
        mutated.loc[mutated["trade_date"] > signal_date, "close"] = 10_000
        mutated.loc[mutated["trade_date"] > signal_date, "open"] = 9_999
        changed = rank_universe_on_date(
            {"000001": mutated, "600000": down}, signal_date, self.config
        )

        self.assertEqual(baseline[0].stock_code, changed[0].stock_code)
        self.assertEqual(baseline[0].score, changed[0].score)
        self.assertEqual(baseline[0].factors, changed[0].factors)

    def test_insufficient_history_is_rejected(self):
        result = compute_raw_factors(make_bars(100), self.config)
        self.assertFalse(result["eligible"])
        self.assertIn("不足", result["rejection_reason"])


if __name__ == "__main__":
    unittest.main()

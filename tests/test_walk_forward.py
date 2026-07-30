import unittest
from datetime import date

from test_backtest_engine import synthetic_bars
from walk_forward import run_walk_forward


def periodic_signal_scorer(history, _price, _change):
    close = int(float(history.iloc[-1]["收盘"]))
    return (80, "periodic") if close % 20 == 0 else (50, "periodic")


class WalkForwardTests(unittest.TestCase):
    def test_rejects_overlapping_train_and_validation_periods(self):
        with self.assertRaisesRegex(ValueError, "不能重叠"):
            run_walk_forward(
                {"000001": synthetic_bars(200)},
                periodic_signal_scorer,
                "v2",
                date(2026, 1, 1),
                date(2026, 4, 30),
                date(2026, 4, 1),
                date(2026, 6, 30),
            )

    def test_selects_on_training_and_limits_validation_signals(self):
        result = run_walk_forward(
            {"000001": synthetic_bars(200)},
            periodic_signal_scorer,
            "v2",
            train_start=date(2026, 1, 1),
            train_end=date(2026, 4, 30),
            validation_start=date(2026, 5, 1),
            validation_end=date(2026, 7, 15),
            thresholds=(60, 70, 80),
            horizon=5,
            minimum_training_trades=1,
            commission_rate=0,
            stamp_duty_rate=0,
            slippage_rate=0,
        )

        self.assertEqual(result.selected_threshold, 80)
        trades = result.validation.stock_results["000001"]["v2"].trades[5]
        self.assertTrue(trades)
        self.assertTrue(
            all(trade.signal_date >= result.validation_start for trade in trades)
        )

    def test_refuses_candidates_with_insufficient_samples(self):
        with self.assertRaisesRegex(ValueError, "拒绝选择参数"):
            run_walk_forward(
                {"000001": synthetic_bars(200)},
                periodic_signal_scorer,
                "v2",
                train_start=date(2026, 1, 1),
                train_end=date(2026, 4, 30),
                validation_start=date(2026, 5, 1),
                validation_end=date(2026, 7, 15),
                thresholds=(80,),
                horizon=5,
                minimum_training_trades=100,
            )


if __name__ == "__main__":
    unittest.main()

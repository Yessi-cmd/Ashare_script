import unittest
from unittest.mock import Mock, patch

import pandas as pd

from strategies import (
    _calc_rsi,
    _history_cache,
    check_portfolio,
    fetch_history,
    fetch_realtime_quotes,
    run_all_checks,
)


class StrategyTests(unittest.TestCase):
    @patch("strategies.calculate_score", return_value=(76, "模拟盘评分"))
    def test_paper_only_symbol_is_scored_without_generating_alerts(self, calculate):
        quotes = pd.DataFrame([{
            "代码": "600519",
            "名称": "贵州茅台",
            "最新价": 10.0,
            "涨跌幅": 1.0,
        }])

        alerts, details = run_all_checks(
            quotes,
            {
                "portfolio": {},
                "watchlist": {},
                "_paper_codes": {"600519"},
                "signal": {"buy_threshold": 70, "sell_threshold": 30},
            },
        )

        self.assertEqual(alerts, [])
        self.assertEqual(details, {"600519": (76, "模拟盘评分")})
        calculate.assert_called_once_with("600519", 10.0, 1.0)

    def test_rsi_for_strictly_rising_series_is_overbought(self):
        closes = pd.Series(range(1, 31), dtype=float)
        self.assertEqual(_calc_rsi(closes), 100.0)

    def test_rsi_for_flat_series_is_neutral(self):
        closes = pd.Series([10.0] * 30)
        self.assertEqual(_calc_rsi(closes), 50.0)

    def test_stop_loss_alert_contains_loss_amount(self):
        row = pd.Series({"代码": "600519", "最新价": 90.0})
        holding = {
            "name": "测试股票",
            "buy_price": 100.0,
            "shares": 100,
            "stop_loss": -5.0,
            "take_profit": 10.0,
        }
        alerts = check_portfolio(row, holding)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].alert_type, "stop_loss")
        self.assertEqual(alerts[0].extra["profit_amount"], -1000.0)

    @patch("strategies.requests.get")
    @patch("strategies.ak.stock_zh_a_spot_em")
    def test_realtime_quotes_prefer_requested_tencent_symbols(
        self,
        mock_eastmoney,
        mock_get,
    ):
        payload = (
            'v_sh600519="1~贵州茅台~600519~1411.00~1400.00~1398.00~12345~";\n'
            'v_sz300750="1~宁德时代~300750~252.00~250.00~249.00~23456~";'
        )
        response = Mock()
        response.content = payload.encode("gb18030")
        response.raise_for_status.return_value = None
        mock_get.return_value = response

        quotes = fetch_realtime_quotes(["300750", "600519"])

        self.assertIsNotNone(quotes)
        self.assertEqual(set(quotes["代码"]), {"300750", "600519"})
        maotai = quotes.loc[quotes["代码"] == "600519"].iloc[0]
        self.assertEqual(maotai["名称"], "贵州茅台")
        self.assertEqual(maotai["最新价"], 1411.0)
        self.assertAlmostEqual(maotai["涨跌幅"], 11 / 1400 * 100)
        requested_url = mock_get.call_args.args[0]
        self.assertIn("sh600519", requested_url)
        self.assertIn("sz300750", requested_url)
        mock_eastmoney.assert_not_called()

    @patch("strategies._fetch_tencent_quotes", return_value=None)
    @patch("strategies.ak.stock_zh_a_spot_em")
    def test_realtime_quotes_fall_back_to_eastmoney(
        self,
        mock_eastmoney,
        _mock_tencent,
    ):
        mock_eastmoney.return_value = pd.DataFrame([
            {"代码": "600519", "名称": "贵州茅台", "最新价": 1411.0, "涨跌幅": 0.79},
            {"代码": "000001", "名称": "平安银行", "最新价": 10.0, "涨跌幅": 0.1},
        ])

        quotes = fetch_realtime_quotes(["600519"])

        self.assertIsNotNone(quotes)
        self.assertEqual(quotes["代码"].tolist(), ["600519"])

    @patch("strategies.ak.stock_zh_a_hist")
    @patch("strategies.load_daily_bars")
    def test_fetch_history_prefers_local_daily_bars(self, mock_load, mock_network):
        _history_cache.clear()
        mock_load.return_value = pd.DataFrame({
            "close": [float(value) for value in range(1, 31)],
            "volume": [1000.0] * 30,
        })

        result = fetch_history("600519", days=20)

        self.assertEqual(len(result), 20)
        self.assertIn("收盘", result.columns)
        self.assertIn("成交量", result.columns)
        mock_network.assert_not_called()


if __name__ == "__main__":
    unittest.main()

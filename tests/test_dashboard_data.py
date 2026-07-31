import unittest
from datetime import date, datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import dashboard_data
from database import Base, DailyBar, MarketQuoteSnapshot
from dashboard_data import (
    _candidate_explanation,
    _candidate_label,
    load_a_share_overview,
    load_market_overview,
    load_recommendations,
    load_screener,
)


def candidate(code, score, momentum, volatility, above_ma20=True):
    return {
        "code": code,
        "score": score,
        "momentum_20": momentum,
        "volatility": volatility,
        "above_ma20": above_ma20,
        "volume_ratio": 1.0,
        "drawdown_60": -5.0,
    }


class DashboardDataTests(unittest.TestCase):
    def test_candidate_labels_require_trend_confirmation(self):
        self.assertEqual(_candidate_label(75, 5, True), "重点研究")
        self.assertEqual(_candidate_label(75, -1, True), "进入观察")
        self.assertEqual(_candidate_label(75, 5, False), "耐心等待")
        self.assertEqual(_candidate_label(30, 5, True), "风险回避")

    def test_explanation_always_contains_counter_argument(self):
        row = candidate("600519", 75, 8, 20)
        reasons, objections = _candidate_explanation(row)
        self.assertIn("V2 技术评分 75 分", reasons)
        self.assertTrue(objections)
        self.assertIn("不包含基本面", objections[0])

    def test_screener_combines_all_filters_and_limit(self):
        data = {
            "as_of": "2026-07-17",
            "universe_size": 19,
            "eligible_size": 4,
            "candidates": [
                candidate("A", 80, 10, 20),
                candidate("B", 70, -2, 20),
                candidate("C", 70, 10, 60),
                candidate("D", 70, 10, 20, above_ma20=False),
            ],
        }
        with patch("dashboard_data._research_candidates", return_value=data):
            result = load_screener(
                {},
                min_score=65,
                min_momentum=0,
                max_volatility=40,
                above_ma20=True,
                limit=1,
            )
        self.assertEqual([row["code"] for row in result["candidates"]], ["A"])
        self.assertEqual(result["match_count"], 1)
        self.assertTrue(result["filters"]["above_ma20"])

    def test_recommendation_limit_does_not_change_eligible_count(self):
        data = {
            "as_of": "2026-07-17",
            "universe_size": 19,
            "eligible_size": 2,
            "candidates": [
                candidate("A", 80, 10, 20),
                candidate("B", 70, 5, 20),
            ],
        }
        with patch("dashboard_data._research_candidates", return_value=data):
            result = load_recommendations({}, limit=1)
        self.assertEqual(len(result["candidates"]), 1)
        self.assertEqual(result["eligible_size"], 2)


class MarketDashboardDataTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        self.get_db_patch = patch("dashboard_data.get_db", side_effect=self.sessions)
        self.init_patch = patch("dashboard_data.init_db")
        self.get_db_patch.start()
        self.init_patch.start()
        dashboard_data._candidates_cache.clear()

    def tearDown(self):
        dashboard_data._candidates_cache.clear()
        self.init_patch.stop()
        self.get_db_patch.stop()
        self.engine.dispose()

    def test_market_overview_groups_indices_and_marks_stale_rows(self):
        # 02:00 UTC = 10:00 香港时间，港股交易中；快照以 UTC 存储。
        now = datetime(2026, 7, 30, 2, 0)
        with self.sessions() as db:
            db.add_all([
                MarketQuoteSnapshot(
                    market="hk",
                    symbol="^HSI",
                    name="恒生指数",
                    price=18_000,
                    change_pct=1.5,
                    currency="HKD",
                    quote_at=now,
                    market_at=now,
                    source="test",
                ),
                MarketQuoteSnapshot(
                    market="hk",
                    symbol="^HSCE",
                    name="恒生国企",
                    price=6_000,
                    change_pct=-1.5,
                    currency="HKD",
                    quote_at=now - timedelta(hours=2),
                    market_at=now,
                    source="test",
                ),
            ])
            db.commit()

        config = {
            "global_markets": {
                "markets": {
                    "hk": {
                        "indices": [
                            {"symbol": "^HSI", "name": "恒生指数"},
                            {"symbol": "^HSCE", "name": "恒生国企"},
                        ]
                    },
                    "kr": {"enabled": False},
                    "us": {"enabled": False},
                    "jp": {"enabled": False},
                }
            }
        }
        with patch("dashboard_data._utc_now", return_value=now):
            result = load_market_overview(config)
        self.assertEqual(len(result["markets"]), 1)
        indices = result["markets"][0]["indices"]
        self.assertEqual(indices[0]["snapshot"]["change_pct"], 1.5)
        self.assertFalse(indices[0]["snapshot"]["stale"])
        self.assertTrue(indices[1]["snapshot"]["stale"])
        # 采集时间以 UTC 存储、按北京时间展示（02:00 UTC = 10:00 北京）。
        self.assertEqual(indices[0]["snapshot"]["quote_at"], datetime(2026, 7, 30, 10, 0))
        # 市场时间按市场自身时区展示（香港 = 北京时间）。
        self.assertEqual(indices[0]["snapshot"]["market_at"], datetime(2026, 7, 30, 10, 0))

    def test_market_stale_follows_market_session(self):
        # 美股快照在闭市时段是“最新收盘”，不应被标为过期。
        config = {
            "global_markets": {
                "markets": {
                    "hk": {"enabled": False},
                    "kr": {"enabled": False},
                    "jp": {"enabled": False},
                }
            }
        }
        quote_at = datetime(2026, 7, 30, 0, 0)
        with self.sessions() as db:
            db.add(MarketQuoteSnapshot(
                market="us",
                symbol="^GSPC",
                name="标普 500",
                price=5_000,
                change_pct=1.0,
                currency="USD",
                quote_at=quote_at,
                market_at=quote_at,
                source="test",
            ))
            db.commit()

        # 02:00 UTC = 前一日 22:00 美东，闭市：2 小时前收盘不算过期。
        with patch("dashboard_data._utc_now", return_value=datetime(2026, 7, 30, 2, 0)):
            result = load_market_overview(config)
        self.assertFalse(result["markets"][0]["indices"][0]["snapshot"]["stale"])

        # 17:00 UTC = 13:00 美东，交易中：2 小时未更新算过期。
        with patch("dashboard_data._utc_now", return_value=datetime(2026, 7, 30, 17, 0)):
            result = load_market_overview(config)
        self.assertTrue(result["markets"][0]["indices"][0]["snapshot"]["stale"])

        # 闭市但已超过 7 天没有新快照：采集器疑似停摆，仍判过期。
        with self.sessions() as db:
            stale_row = db.get(MarketQuoteSnapshot, ("us", "^GSPC"))
            stale_row.quote_at = datetime(2026, 7, 20, 0, 0)
            stale_row.market_at = datetime(2026, 7, 20, 0, 0)
            db.commit()
        with patch("dashboard_data._utc_now", return_value=datetime(2026, 7, 30, 2, 0)):
            result = load_market_overview(config)
        self.assertTrue(result["markets"][0]["indices"][0]["snapshot"]["stale"])

    def test_a_share_overview_uses_local_bars_for_trend_and_live_quote_for_today(self):
        # 02:00 UTC = 10:00 北京时间，A股交易中。
        now = datetime(2026, 7, 30, 2, 0)
        definitions = {
            "000001": ("000001.SS", "上证指数"),
            "399001": ("399001.SZ", "深证成指"),
            "399006": ("399006.SZ", "创业板指"),
            "000300": ("000300.SS", "沪深 300"),
        }
        with self.sessions() as db:
            for code, (symbol, name) in definitions.items():
                for index in range(25):
                    close = 100 + index
                    db.add(DailyBar(
                        stock_code=code,
                        trade_date=date(2026, 7, 1) + timedelta(days=index),
                        adjust="",
                        open=close,
                        high=close + 1,
                        low=close - 1,
                        close=close,
                        volume=1_000,
                        source="test",
                        fetched_at=now,
                    ))
                db.add(MarketQuoteSnapshot(
                    market="a_share",
                    symbol=symbol,
                    name=name,
                    price=125,
                    change_pct=1.25,
                    currency="CNY",
                    quote_at=now,
                    market_at=now,
                    source="test",
                ))
            db.commit()

        with patch("dashboard_data._utc_now", return_value=now):
            result = load_a_share_overview({})
        self.assertEqual(len(result["indices"]), 4)
        first = result["indices"][0]
        self.assertEqual(first["price"], 125)
        self.assertEqual(first["data_status"], "实时")
        self.assertGreater(first["change_20"], 15)
        self.assertTrue(first["above_ma20"])
        self.assertTrue(first["sparkline"])
        self.assertEqual(result["summary"]["label"], "偏强")

    def test_research_candidates_are_computed_once_per_trading_date(self):
        latest = date(2026, 5, 1) + timedelta(days=64)
        rows = []
        for code in ("600519", "000001", "300750"):
            for index in range(65):
                close = 100.0 + index
                rows.append(DailyBar(
                    stock_code=code,
                    trade_date=date(2026, 5, 1) + timedelta(days=index),
                    adjust="qfq",
                    open=close,
                    high=close + 1,
                    low=close - 1,
                    close=close,
                    volume=1_000,
                    source="test",
                    fetched_at=datetime(2026, 7, 30, 2, 0),
                ))
        with self.sessions() as db:
            db.add_all(rows)
            db.commit()

        with patch(
            "dashboard_data._compute_universe_scores",
            wraps=dashboard_data._compute_universe_scores,
        ) as compute:
            first = load_recommendations({})
            second = load_recommendations({})
        self.assertEqual(first["as_of"], latest)
        self.assertEqual(second["as_of"], latest)
        self.assertEqual(len(first["candidates"]), 3)
        # 同一交易日多次请求共享一次全量计算，第二次直接命中缓存。
        self.assertEqual(compute.call_count, 1)
        self.assertIn(first["candidates"][0]["name"], dashboard_data.RESEARCH_UNIVERSE.values())


if __name__ == "__main__":
    unittest.main()

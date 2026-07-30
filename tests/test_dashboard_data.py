import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base, MarketQuoteSnapshot
from dashboard_data import (
    _candidate_explanation,
    _candidate_label,
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

    def tearDown(self):
        self.init_patch.stop()
        self.get_db_patch.stop()
        self.engine.dispose()

    def test_market_overview_groups_indices_and_marks_stale_rows(self):
        now = datetime.now()
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
        result = load_market_overview(config)
        self.assertEqual(len(result["markets"]), 1)
        indices = result["markets"][0]["indices"]
        self.assertEqual(indices[0]["snapshot"]["change_pct"], 1.5)
        self.assertFalse(indices[0]["snapshot"]["stale"])
        self.assertTrue(indices[1]["snapshot"]["stale"])


if __name__ == "__main__":
    unittest.main()

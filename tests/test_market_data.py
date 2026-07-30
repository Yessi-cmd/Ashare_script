import unittest
from datetime import date
from unittest.mock import patch

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base, DailyBar
from market_data import (
    MarketDataError,
    _index_exchange_symbol,
    load_daily_bars,
    normalize_adjust,
    normalize_daily_bars,
    sync_daily_bars,
    upsert_daily_bars,
)


class MarketDataTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        self.get_db_patch = patch("market_data.get_db", side_effect=self.sessions)
        self.get_db_patch.start()

    def tearDown(self):
        self.get_db_patch.stop()
        self.engine.dispose()

    @staticmethod
    def sample_frame(close=10.5):
        return pd.DataFrame([{
            "日期": "2026-07-17",
            "开盘": 10.0,
            "最高": 11.0,
            "最低": 9.8,
            "收盘": close,
            "成交量": 1000,
            "成交额": 10500,
        }])

    def test_normalize_raw_adjustment(self):
        self.assertEqual(normalize_adjust("raw"), "")

    def test_index_exchange_symbol_is_explicit(self):
        self.assertEqual(_index_exchange_symbol("000300"), "sh000300")
        self.assertEqual(_index_exchange_symbol("399001"), "sz399001")

    def test_upsert_is_idempotent_and_updates_values(self):
        first = normalize_daily_bars(self.sample_frame(), "1")
        second = normalize_daily_bars(self.sample_frame(close=10.8), "1")
        upsert_daily_bars(first)
        upsert_daily_bars(second)

        with self.sessions() as db:
            self.assertEqual(db.query(DailyBar).count(), 1)
        bars = load_daily_bars("000001", date(2026, 7, 1), date(2026, 7, 31))
        self.assertEqual(float(bars.iloc[0]["close"]), 10.8)

    def test_invalid_price_relationship_is_rejected(self):
        frame = self.sample_frame()
        frame.loc[0, "最高"] = 9.0
        with self.assertRaises(MarketDataError):
            normalize_daily_bars(frame, "000001")

    def test_sync_failure_preserves_existing_cache(self):
        upsert_daily_bars(normalize_daily_bars(self.sample_frame(), "1"))
        with patch("market_data.time.sleep"), patch(
            "market_data.ak.stock_zh_a_hist", side_effect=RuntimeError("offline")
        ):
            with self.assertRaisesRegex(MarketDataError, "offline"):
                sync_daily_bars(
                    "1", date(2026, 7, 1), date(2026, 7, 31), source="eastmoney"
                )
        bars = load_daily_bars("1")
        self.assertEqual(len(bars), 1)


if __name__ == "__main__":
    unittest.main()

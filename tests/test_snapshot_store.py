import unittest
from unittest.mock import patch

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base, QuoteSnapshot
from snapshot_store import save_quote_snapshots


class SnapshotStoreTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        self.get_db_patch = patch("snapshot_store.get_db", side_effect=self.sessions)
        self.init_patch = patch("snapshot_store.init_db")
        self.get_db_patch.start()
        self.init_patch.start()

    def tearDown(self):
        self.init_patch.stop()
        self.get_db_patch.stop()
        self.engine.dispose()

    def test_snapshot_upsert_is_atomic_and_idempotent(self):
        first = pd.DataFrame([{
            "代码": "1",
            "名称": "平安银行",
            "最新价": 10.0,
            "涨跌幅": 1.2,
            "成交量": 1000,
        }])
        second = first.copy()
        second.loc[0, "最新价"] = 10.5

        save_quote_snapshots(first, {"000001": (72, "首次评分")})
        save_quote_snapshots(second, {"000001": (75, "更新评分")})

        with self.sessions() as db:
            rows = db.query(QuoteSnapshot).all()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].price, 10.5)
            self.assertEqual(rows[0].score, 75)
            self.assertEqual(rows[0].reason, "更新评分")


if __name__ == "__main__":
    unittest.main()

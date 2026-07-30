import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base, Portfolio, User
from user_config import load_user_config, save_user_config


class UserConfigTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        self.get_db_patch = patch("user_config.get_db", side_effect=self.sessions)
        self.get_db_patch.start()

    def tearDown(self):
        self.get_db_patch.stop()
        self.engine.dispose()

    def test_read_without_create_has_no_side_effect(self):
        config = load_user_config(123, create_user=False)
        self.assertEqual(config["portfolio"], {})
        with self.sessions() as db:
            self.assertEqual(db.query(User).count(), 0)

    def test_save_updates_one_holding_atomically(self):
        config = {
            "portfolio": {
                "600519": {
                    "name": "贵州茅台",
                    "buy_price": 1500,
                    "shares": 100,
                    "stop_loss": -5,
                    "take_profit": 10,
                }
            },
            "watchlist": {},
        }
        save_user_config(123, config)
        config["portfolio"]["600519"]["shares"] = 200
        save_user_config(123, config)

        with self.sessions() as db:
            rows = db.query(Portfolio).all()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].shares, 200)


if __name__ == "__main__":
    unittest.main()

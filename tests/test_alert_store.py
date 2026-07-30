import unittest
from dataclasses import dataclass
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from alert_store import load_alert_cache, mark_alerted
from database import Base


@dataclass
class FakeAlert:
    stock_code: str
    alert_type: str


class AlertStoreTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        self.get_db_patch = patch("alert_store.get_db", side_effect=self.sessions)
        self.get_db_patch.start()

    def tearDown(self):
        self.get_db_patch.stop()
        self.engine.dispose()

    def test_alert_cooldown_survives_reload(self):
        alert = FakeAlert(stock_code="600519", alert_type="stop_loss")
        mark_alerted(123, [alert], 1000.0)
        self.assertEqual(load_alert_cache(123)["600519:stop_loss"], 1000.0)


if __name__ == "__main__":
    unittest.main()

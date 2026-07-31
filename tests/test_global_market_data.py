import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base, MarketQuoteSnapshot
from global_market_data import (
    DEFAULT_A_SHARE_DEFINITIONS,
    DEFAULT_MARKET_DEFINITIONS,
    MarketQuote,
    fetch_market_quotes,
    market_definitions,
    parse_yahoo_chart_payload,
    save_market_snapshots,
)


def yahoo_payload(price=100.0, previous_close=98.0, timestamp=1785000000):
    return {
        "chart": {
            "result": [{
                "meta": {
                    "regularMarketPrice": price,
                    "previousClose": previous_close,
                    "regularMarketTime": timestamp,
                }
            }],
            "error": None,
        }
    }


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class GlobalMarketDataTests(unittest.TestCase):
    def test_default_definitions_cover_four_markets_and_six_indices(self):
        definitions = market_definitions({})
        self.assertEqual([item.market for item in definitions], [
            "hk", "kr", "us", "us", "us", "jp"
        ])
        self.assertEqual(len(definitions), 6)

    def test_market_override_is_applied_once_per_market(self):
        config = {
            "global_markets": {
                "markets": {
                    "hk": {"enabled": False},
                    "us": {"indices": [{"symbol": "^NDX", "name": "纳斯达克 100"}]},
                }
            }
        }
        definitions = market_definitions(config)
        self.assertNotIn("hk", {item.market for item in definitions})
        us = [item for item in definitions if item.market == "us"]
        self.assertEqual(len(us), 1)
        self.assertEqual(us[0].symbol, "^NDX")

    def test_a_share_definitions_align_yahoo_symbols_with_local_codes(self):
        definitions = market_definitions({}, include_a_share=True)
        a_share = [item for item in definitions if item.market == "a_share"]
        self.assertEqual(len(a_share), 4)
        self.assertEqual(
            [(item.symbol, item.local_code) for item in a_share],
            [("000001.SS", "000001"), ("399001.SZ", "399001"),
             ("399006.SZ", "399006"), ("000300.SS", "000300")],
        )
        self.assertEqual(DEFAULT_A_SHARE_DEFINITIONS[-1].local_code, "000300")

    def test_parse_uses_previous_close_and_stores_utc_market_time(self):
        definition = DEFAULT_MARKET_DEFINITIONS[0]
        quote = parse_yahoo_chart_payload(
            yahoo_payload(price=18_500, previous_close=18_000),
            definition,
            quote_at=datetime(2026, 7, 30, 12, 0),
        )
        self.assertAlmostEqual(quote.change_pct, 2.777777, places=4)
        self.assertEqual(quote.quote_at, datetime(2026, 7, 30, 12, 0))
        self.assertEqual(
            quote.market_at,
            datetime.fromtimestamp(1785000000, tz=timezone.utc).replace(tzinfo=None),
        )

    def test_parse_falls_back_to_chart_close_and_chart_previous_close(self):
        definition = DEFAULT_MARKET_DEFINITIONS[0]
        payload = {
            "chart": {
                "result": [{
                    "meta": {
                        "regularMarketPrice": -1,
                        "chartPreviousClose": 98,
                    },
                    "timestamp": [1785000000],
                    "indicators": {"quote": [{"close": [99]}]},
                }],
                "error": None,
            }
        }
        quote = parse_yahoo_chart_payload(payload, definition)
        self.assertEqual(quote.price, 99)
        self.assertAlmostEqual(quote.change_pct, 1.020408, places=4)

    def test_fetch_isolates_one_failed_index(self):
        def fake_get(url, **_kwargs):
            if "%5EKS11" in url:
                raise RuntimeError("模拟限流")
            return FakeResponse(yahoo_payload())

        quotes, errors = fetch_market_quotes(
            DEFAULT_MARKET_DEFINITIONS,
            http_get=fake_get,
        )
        self.assertEqual(len(quotes), 5)
        self.assertEqual(len(errors), 1)
        self.assertIn("模拟限流", errors[0])

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        self.get_db_patch = patch(
            "global_market_data.get_db", side_effect=self.sessions
        )
        self.init_patch = patch("global_market_data.init_db")
        self.get_db_patch.start()
        self.init_patch.start()

    def tearDown(self):
        self.init_patch.stop()
        self.get_db_patch.stop()
        self.engine.dispose()

    def test_snapshot_upsert_keeps_one_row_per_market_symbol(self):
        first = MarketQuote(
            market="us",
            symbol="^GSPC",
            name="标普 500",
            price=5_000,
            change_pct=1.2,
            currency="USD",
            quote_at=datetime(2026, 7, 30, 10, 0),
            market_at=datetime(2026, 7, 30, 3, 0),
        )
        second = MarketQuote(
            **{**first.__dict__, "price": 5_050, "change_pct": 2.2}
        )
        self.assertEqual(save_market_snapshots([first]), 1)
        self.assertEqual(save_market_snapshots([second]), 1)
        with self.sessions() as db:
            rows = db.query(MarketQuoteSnapshot).all()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].price, 5_050)
            self.assertEqual(rows[0].change_pct, 2.2)


if __name__ == "__main__":
    unittest.main()

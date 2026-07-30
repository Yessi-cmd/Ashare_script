import time
import unittest
from unittest.mock import patch

import pandas as pd
from fastapi.testclient import TestClient

from web_app import SESSION_COOKIE_NAME, _make_session_token, app


EMPTY_RECOMMENDATIONS = {
    "as_of": None,
    "candidates": [],
    "universe_size": 19,
    "eligible_size": 0,
    "model": "test",
}
EMPTY_OVERVIEW = {
    "portfolio": [],
    "watchlist": [],
    "total_cost": 0.0,
    "total_value": 0.0,
    "total_profit": 0.0,
    "total_profit_pct": 0.0,
    "latest_quote_at": None,
    "stale_after_seconds": 120,
}
EMPTY_MARKETS = {
    "enabled": True,
    "markets": [],
    "latest_quote_at": None,
    "stale_after_seconds": 900,
    "source": "Yahoo Finance Chart API",
}
FULL_MARKETS = {
    "enabled": True,
    "markets": [{
        "key": "hk",
        "name": "港股",
        "currency": "HKD",
        "timezone": "Asia/Hong_Kong",
        "latest_quote_at": "2026-07-30 12:00:00",
        "indices": [{
            "symbol": "^HSI",
            "name": "恒生指数",
            "snapshot": {
                "price": 25858.88,
                "change_pct": 0.2,
                "quote_at": "2026-07-30 12:00:00",
                "market_at": "2026-07-30 11:59:00",
                "stale": False,
            },
        }],
    }],
    "latest_quote_at": "2026-07-30 12:00:00",
    "stale_after_seconds": 900,
    "source": "Yahoo Finance Chart API",
}
AUTH_ENV = {
    "ASHARE_WEB_USERNAME": "owner",
    "ASHARE_WEB_PASSWORD": "secret",
    "ASHARE_WEB_SESSION_SECRET": "0123456789abcdef0123456789abcdef",
}


class WebAppTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app, follow_redirects=False)

    def _overview_patches(self):
        return (
            patch("web_app._config", return_value={}),
            patch("web_app.load_overview", return_value=EMPTY_OVERVIEW.copy()),
            patch(
                "web_app.load_recommendations",
                return_value=EMPTY_RECOMMENDATIONS.copy(),
            ),
        )

    def test_health_check_has_no_sensitive_details(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_pages_fail_closed_when_session_secret_is_missing(self):
        env = {"ASHARE_WEB_USERNAME": "owner", "ASHARE_WEB_PASSWORD": "secret"}
        with patch.dict("os.environ", env, clear=True):
            response = self.client.get("/")
        self.assertEqual(response.status_code, 503)

    def test_regular_page_redirects_to_chinese_login(self):
        with patch.dict("os.environ", AUTH_ENV, clear=True):
            response = self.client.get("/recommendations?tab=today")
            login = self.client.get(response.headers["location"])
        self.assertEqual(response.status_code, 303)
        self.assertIn("/login?next=", response.headers["location"])
        self.assertEqual(login.status_code, 200)
        self.assertIn("登录研究台", login.text)

    def test_wrong_login_is_rejected(self):
        with patch.dict("os.environ", AUTH_ENV, clear=True):
            response = self.client.post(
                "/login",
                data={"username": "owner", "password": "wrong", "remember": "yes"},
            )
        self.assertEqual(response.status_code, 401)
        self.assertIn("用户名或密码不正确", response.text)
        self.assertNotIn(SESSION_COOKIE_NAME, response.cookies)

    def test_login_cookie_keeps_page_access(self):
        config_patch, overview_patch, recommendation_patch = self._overview_patches()
        with patch.dict("os.environ", AUTH_ENV, clear=True), config_patch, \
                overview_patch, recommendation_patch:
            logged_in = self.client.post(
                "/login",
                data={
                    "username": "owner",
                    "password": "secret",
                    "remember": "yes",
                    "next": "/",
                },
            )
            page = self.client.get("/")
        self.assertEqual(logged_in.status_code, 303)
        self.assertIn(SESSION_COOKIE_NAME, logged_in.cookies)
        self.assertIn("Max-Age=2592000", logged_in.headers["set-cookie"])
        self.assertEqual(page.status_code, 200)
        self.assertIn("今日重点候选", page.text)

    def test_tampered_and_expired_cookies_are_rejected(self):
        expired = _make_session_token(
            "owner", AUTH_ENV["ASHARE_WEB_SESSION_SECRET"], int(time.time()) - 1
        )
        with patch.dict("os.environ", AUTH_ENV, clear=True):
            for token in ("tampered.value", expired):
                self.client.cookies.set(SESSION_COOKIE_NAME, token)
                response = self.client.get("/")
                self.assertEqual(response.status_code, 303)

    def test_basic_auth_remains_available_for_api(self):
        bars = pd.DataFrame(
            [
                {
                    "trade_date": pd.Timestamp("2026-07-17").date(),
                    "open": 10.0,
                    "high": 11.0,
                    "low": 9.5,
                    "close": 10.5,
                    "volume": 100.0,
                }
            ]
        )
        with patch.dict("os.environ", AUTH_ENV, clear=True), patch(
            "web_app.load_daily_bars", return_value=bars
        ):
            rejected = self.client.get("/api/stocks/600519/bars")
            accepted = self.client.get(
                "/api/stocks/600519/bars", auth=("owner", "secret")
            )
        self.assertEqual(rejected.status_code, 401)
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.json()["stock_code"], "600519")

    def test_markets_page_uses_authenticated_local_loader(self):
        with patch.dict("os.environ", AUTH_ENV, clear=True), patch(
            "web_app._config", return_value={}
        ), patch("web_app.load_market_overview", return_value=EMPTY_MARKETS):
            rejected = self.client.get("/markets")
            accepted = self.client.get("/markets", auth=("owner", "secret"))
        self.assertEqual(rejected.status_code, 303)
        self.assertEqual(accepted.status_code, 200)
        self.assertIn("全球市场", accepted.text)
        self.assertIn("Yahoo Finance Chart API", accepted.text)

    def test_markets_page_renders_quote_cards(self):
        with patch.dict("os.environ", AUTH_ENV, clear=True), patch(
            "web_app._config", return_value={}
        ), patch("web_app.load_market_overview", return_value=FULL_MARKETS):
            response = self.client.get("/markets", auth=("owner", "secret"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("恒生指数", response.text)
        self.assertIn("25858.88", response.text)
        self.assertIn("+0.20%", response.text)

    def test_external_next_redirect_is_blocked(self):
        with patch.dict("os.environ", AUTH_ENV, clear=True):
            response = self.client.post(
                "/login",
                data={
                    "username": "owner",
                    "password": "secret",
                    "remember": "yes",
                    "next": "//evil.example/path",
                },
            )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/")


if __name__ == "__main__":
    unittest.main()

import copy
import time
import unittest
from unittest.mock import patch

import pandas as pd
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

from database import Base, Portfolio, User
from web_app import (
    SESSION_COOKIE_NAME,
    _make_csrf_token,
    _make_session_token,
    app,
)
from web_auth import WebPrincipal


EMPTY_RECOMMENDATIONS = {
    "as_of": None,
    "candidates": [],
    "universe_size": 19,
    "eligible_size": 0,
    "model": "test",
}
SAMPLE_RECOMMENDATIONS = {
    "as_of": "2026-07-30",
    "candidates": [{
        "code": "600519",
        "name": "贵州茅台",
        "label": "进入观察",
        "score": 68,
        "close": 1253.0,
        "momentum_20": 5.6,
        "volatility": 22.0,
        "drawdown_60": -12.1,
        "reasons": ["V2 技术评分 68 分", "收盘价站上 20 日均线"],
        "objections": ["近期量能没有明显放大"],
        "invalidation_price": 1168.63,
        "is_portfolio": True,
        "is_watchlist": False,
    }],
    "universe_size": 19,
    "eligible_size": 1,
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
OVERVIEW_WITH_INDEX_TAPE = {
    **EMPTY_OVERVIEW,
    "latest_quote_at": "2026-07-30 15:00:00",
    "a_share": {
        "as_of": "2026-07-30",
        "latest_quote_at": "2026-07-30 15:00:00",
        "summary": {
            "label": "偏弱",
            "class": "weak",
            "description": "0/4 个指数位于 20 日均线上方",
        },
        "indices": [{
            "name": "上证指数",
            "local_code": "000001",
            "data_status": "实时",
            "trend_class": "weak",
            "trend_label": "偏弱",
            "ma20_label": "低于 MA20",
            "price": 3804.69,
            "change_pct": -0.23,
            "change_20": -5.56,
            "sparkline": "0,8 90,20 180,46",
            "has_data": True,
        }],
    },
}
AUTH_ENV = {
    "ASHARE_WEB_USERNAME": "owner",
    "ASHARE_WEB_PASSWORD": "secret",
    "ASHARE_WEB_SESSION_SECRET": "0123456789abcdef0123456789abcdef",
}
EMPTY_PAPER = {
    "owner_user_id": 0,
    "account": {
        "initial_cash": 10_000.0,
        "cash": 10_000.0,
        "market_value": 0.0,
        "equity": 10_000.0,
        "total_profit": 0.0,
        "total_profit_pct": 0.0,
        "realized_profit": 0.0,
    },
    "positions": [],
    "orders": [],
    "pending_count": 0,
    "market_open": False,
    "latest_quote_at": None,
    "stale_after_seconds": 120,
    "live": {
        "holding_count": 0,
        "priced_count": 0,
        "total_cost": 0.0,
        "total_value": 0.0,
        "total_profit": 0.0,
        "total_profit_pct": 0.0,
    },
    "rules": {"lot_size": 100, "stop_loss_pct": -5, "take_profit_pct": 10},
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
        class FakeDb:
            def query(self, *_args, **_kwargs):
                return self

            def scalar(self):
                return 0

            def close(self):
                return None

        with patch("web_app._config", return_value={}), patch(
            "web_app.init_db"
        ), patch("web_app.get_db", return_value=FakeDb()):
            response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_health_check_fails_when_database_unavailable(self):
        with patch("web_app._config", return_value={}), patch(
            "web_app.init_db", side_effect=RuntimeError("数据库被锁")
        ):
            response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("数据库被锁", response.text)

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

    def test_authenticated_pages_pass_current_user_to_personal_loaders(self):
        principal = WebPrincipal(user_id=41, username="alice")
        with patch.dict(
            "os.environ",
            {"ASHARE_WEB_SESSION_SECRET": "0123456789abcdef0123456789abcdef"},
            clear=True,
        ), patch("web_app.authenticate_web_user", return_value=principal), patch(
            "web_app._config", return_value={}
        ), patch(
            "web_app.load_overview", return_value=EMPTY_OVERVIEW.copy()
        ) as overview, patch(
            "web_app.load_recommendations", return_value=EMPTY_RECOMMENDATIONS.copy()
        ) as recommendations:
            response = self.client.get("/", auth=("alice", "password"))

        self.assertEqual(response.status_code, 200)
        overview.assert_called_once_with({}, user_id=41)
        recommendations.assert_called_once_with({}, user_id=41, limit=4)
        self.assertIn("alice", response.text)

    def test_registration_route_redirects_after_account_creation(self):
        principal = WebPrincipal(user_id=42, username="bob")
        with patch.dict(
            "os.environ",
            {
                "ASHARE_WEB_SESSION_SECRET": "0123456789abcdef0123456789abcdef",
                "ASHARE_WEB_REGISTRATION_CODE": "invite-only",
            },
            clear=True,
        ), patch("web_app.register_web_user", return_value=principal) as register:
            response = self.client.post(
                "/register",
                data={
                    "username": "bob",
                    "password": "bob-password",
                    "password_confirm": "bob-password",
                    "registration_code": "invite-only",
                },
            )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/login?registered=1")
        register.assert_called_once_with("bob", "bob-password", "invite-only")

    def test_admin_page_is_hidden_from_regular_users(self):
        regular = WebPrincipal(user_id=41, username="alice")
        with patch.dict("os.environ", AUTH_ENV, clear=True), patch(
            "web_app.authenticate_web_user", return_value=regular
        ):
            response = self.client.get("/admin", auth=("alice", "password"))
        self.assertEqual(response.status_code, 403)

    def test_admin_page_renders_users_and_admin_create_uses_csrf(self):
        admin = WebPrincipal(user_id=41, username="owner", is_admin=True)
        users = [{
            "user_id": 41,
            "username": "owner",
            "legacy_env": True,
            "is_admin": True,
            "is_active": True,
            "created_at": None,
            "portfolio_count": 1,
            "watchlist_count": 2,
            "paper_order_count": 3,
        }]
        with patch.dict("os.environ", AUTH_ENV, clear=True), patch(
            "web_app.authenticate_web_user", return_value=admin
        ), patch("web_app.list_web_users", return_value=users):
            page = self.client.get("/admin", auth=("owner", "password"))
        self.assertEqual(page.status_code, 200)
        self.assertIn("用户管理", page.text)
        self.assertIn("添加用户", page.text)
        self.assertIn("owner", page.text)

        csrf_token = _make_csrf_token(
            "owner", AUTH_ENV["ASHARE_WEB_SESSION_SECRET"], int(time.time()) + 60, 41
        )
        with patch.dict("os.environ", AUTH_ENV, clear=True), patch(
            "web_app.authenticate_web_user", return_value=admin
        ), patch("web_app.admin_create_web_user") as create:
            response = self.client.post(
                "/admin/users",
                auth=("owner", "password"),
                data={
                    "csrf_token": csrf_token,
                    "username": "bob",
                    "password": "bob-password",
                    "password_confirm": "bob-password",
                },
            )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/admin?result=user-created")
        create.assert_called_once_with("bob", "bob-password")

        with patch.dict("os.environ", AUTH_ENV, clear=True), patch(
            "web_app.authenticate_web_user", return_value=admin
        ), patch("web_app.admin_create_web_user") as create:
            rejected = self.client.post(
                "/admin/users",
                auth=("owner", "password"),
                data={
                    "csrf_token": "tampered",
                    "username": "mallory",
                    "password": "mallory-password",
                    "password_confirm": "mallory-password",
                },
            )
        self.assertEqual(rejected.status_code, 403)
        create.assert_not_called()

    def test_overview_uses_compact_index_tape_without_hero_slogan(self):
        with patch.dict("os.environ", AUTH_ENV, clear=True), patch(
            "web_app._config", return_value={}
        ), patch("web_app.load_overview", return_value=OVERVIEW_WITH_INDEX_TAPE), patch(
            "web_app.load_recommendations", return_value=EMPTY_RECOMMENDATIONS
        ):
            response = self.client.get("/", auth=("owner", "secret"))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("先看机会，再看风险", response.text)
        self.assertIn("market-tape", response.text)
        self.assertIn('fill="none"', response.text)
        self.assertIn("大盘概览", response.text)
        self.assertIn("data-theme-toggle", response.text)
        self.assertIn("跳到主要内容", response.text)
        self.assertIn("/static/app.js?v=14", response.text)

    def test_shared_navigation_marks_current_page_and_login_has_theme_control(self):
        with patch.dict("os.environ", AUTH_ENV, clear=True), patch(
            "web_app._config", return_value={}
        ), patch("web_app.load_market_overview", return_value=EMPTY_MARKETS):
            page = self.client.get("/markets", auth=("owner", "secret"))
            login = self.client.get("/login")
        self.assertEqual(page.status_code, 200)
        self.assertIn('href="/markets" aria-current="page"', page.text)
        self.assertEqual(login.status_code, 200)
        self.assertIn("data-theme-toggle", login.text)

    def test_paper_page_is_authenticated_and_renders_initial_account(self):
        with patch.dict("os.environ", AUTH_ENV, clear=True), patch(
            "web_app._config", return_value={}
        ), patch(
            "web_app.load_paper_dashboard", return_value=copy.deepcopy(EMPTY_PAPER)
        ):
            rejected = self.client.get("/paper")
            accepted = self.client.get("/paper", auth=("owner", "secret"))
        self.assertEqual(rejected.status_code, 303)
        self.assertEqual(accepted.status_code, 200)
        self.assertIn('href="/paper" aria-current="page"', accepted.text)
        self.assertIn("初始资金", accepted.text)
        self.assertIn("10000.00", accepted.text)

    def test_paper_order_requires_valid_csrf(self):
        with patch.dict("os.environ", AUTH_ENV, clear=True), patch(
            "web_app._config", return_value={}
        ), patch("web_app.submit_paper_order") as submit:
            response = self.client.post(
                "/paper/orders",
                auth=("owner", "secret"),
                data={
                    "side": "BUY",
                    "stock_code": "600519",
                    "quantity": "100",
                    "client_order_id": "order_key_web_1",
                    "csrf_token": "tampered",
                },
            )
        self.assertEqual(response.status_code, 403)
        submit.assert_not_called()

    def test_paper_order_with_csrf_uses_prg_and_owner_key(self):
        csrf_token = _make_csrf_token(
            "owner", AUTH_ENV["ASHARE_WEB_SESSION_SECRET"], int(time.time()) + 60
        )
        with patch.dict("os.environ", AUTH_ENV, clear=True), patch(
            "web_app._config", return_value={"app": {"owner_user_id": 123}}
        ), patch("web_app.submit_paper_order") as submit:
            response = self.client.post(
                "/paper/orders",
                auth=("owner", "secret"),
                data={
                    "side": "BUY",
                    "stock_code": "600519",
                    "quantity": "100",
                    "client_order_id": "order_key_web_2",
                    "csrf_token": csrf_token,
                },
            )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/paper?result=order-created")
        submit.assert_called_once_with(
            123, "BUY", "600519", "100", "order_key_web_2"
        )

    def test_recommendations_use_dense_ranked_rows(self):
        with patch.dict("os.environ", AUTH_ENV, clear=True), patch(
            "web_app._config", return_value={}
        ), patch(
            "web_app.load_recommendations", return_value=SAMPLE_RECOMMENDATIONS
        ):
            response = self.client.get("/recommendations", auth=("owner", "secret"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("recommendation-board", response.text)
        self.assertIn("recommendation-row", response.text)
        self.assertIn(">01<", response.text)
        self.assertIn("贵州茅台", response.text)
        self.assertIn("为什么入选", response.text)
        self.assertIn("反对理由", response.text)

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


class AccountWebAppTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app, follow_redirects=False)
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        with self.sessions() as db:
            db.add_all([
                User(user_id=41, username="alice"),
                User(user_id=42, username="bob"),
            ])
            db.commit()

    def tearDown(self):
        self.engine.dispose()

    def test_account_write_is_scoped_to_authenticated_user(self):
        alice = WebPrincipal(user_id=41, username="alice")
        secret = "0123456789abcdef0123456789abcdef"
        csrf = _make_csrf_token("alice", secret, int(time.time()) + 60, 41)
        with patch.dict(
            "os.environ", {"ASHARE_WEB_SESSION_SECRET": secret}, clear=True
        ), patch("web_app.authenticate_web_user", return_value=alice), patch(
            "web_app._config", return_value={}
        ), patch("web_app.init_db"), patch(
            "web_app.get_db", side_effect=self.sessions
        ):
            saved = self.client.post(
                "/account/portfolio",
                auth=("alice", "password"),
                data={
                    "csrf_token": csrf,
                    "stock_code": "600519",
                    "name": "贵州茅台",
                    "buy_price": "1500",
                    "shares": "100",
                    "stop_loss": "-5",
                    "take_profit": "10",
                },
            )
            self.assertEqual(saved.status_code, 303)

            bob = WebPrincipal(user_id=42, username="bob")
            with patch("web_app.authenticate_web_user", return_value=bob):
                bob_page = self.client.get("/account", auth=("bob", "password"))

        self.assertEqual(bob_page.status_code, 200)
        self.assertNotIn("贵州茅台", bob_page.text)
        with self.sessions() as db:
            self.assertEqual(db.query(Portfolio).filter(
                Portfolio.user_id == 41,
                Portfolio.stock_code == "600519",
            ).count(), 1)
            self.assertEqual(db.query(Portfolio).filter(
                Portfolio.user_id == 42,
            ).count(), 0)


if __name__ == "__main__":
    unittest.main()

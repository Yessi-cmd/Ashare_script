import os
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base, Portfolio, WebUser
from web_auth import (
    WebAuthError,
    authenticate_web_user,
    load_web_principal,
    make_signed_token,
    read_signed_token,
    register_web_user,
    verify_password,
)


class WebAuthTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        self.get_db_patch = patch("web_auth.get_db", side_effect=self.sessions)
        self.init_patch = patch("web_auth.init_db")
        self.get_db_patch.start()
        self.init_patch.start()

    def tearDown(self):
        self.init_patch.stop()
        self.get_db_patch.stop()
        self.engine.dispose()

    def test_each_registered_account_has_an_independent_user_key(self):
        with patch.dict(
            os.environ,
            {"ASHARE_WEB_REGISTRATION_CODE": "invite-only"},
            clear=True,
        ):
            alice = register_web_user("Alice", "alice-password", "invite-only")
            bob = register_web_user("bob", "bob-password", "invite-only")

        self.assertNotEqual(alice.user_id, bob.user_id)
        self.assertEqual(alice.username, "alice")
        self.assertTrue(authenticate_web_user("alice", "alice-password"))
        self.assertIsNone(authenticate_web_user("alice", "bob-password"))

        with self.sessions() as db:
            db.add(Portfolio(
                user_id=alice.user_id,
                stock_code="600519",
                name="贵州茅台",
                buy_price=1500,
                shares=100,
            ))
            db.commit()
            self.assertEqual(
                db.query(Portfolio).filter(
                    Portfolio.user_id == bob.user_id
                ).count(),
                0,
            )

    def test_registration_requires_invite_and_strong_password(self):
        with patch.dict(
            os.environ,
            {"ASHARE_WEB_REGISTRATION_CODE": "invite-only"},
            clear=True,
        ):
            with self.assertRaisesRegex(WebAuthError, "邀请码"):
                register_web_user("alice", "alice-password", "wrong")
            with self.assertRaisesRegex(WebAuthError, "至少需要 8 位"):
                register_web_user("alice", "short", "invite-only")

        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(WebAuthError, "未开放注册"):
                register_web_user("alice", "alice-password", "invite-only")

    def test_session_token_is_bound_to_user_and_purpose(self):
        token = make_signed_token(17, "alice", "a" * 32, 4_000_000_000)
        self.assertEqual(
            read_signed_token(token, "a" * 32),
            {"user_id": 17, "username": "alice"},
        )
        self.assertIsNone(read_signed_token(token, "b" * 32))
        self.assertIsNone(
            read_signed_token(token, "a" * 32, purpose="csrf")
        )
        self.assertIsNone(load_web_principal(17, "bob"))

    def test_password_hash_is_not_plaintext(self):
        with patch.dict(
            os.environ,
            {"ASHARE_WEB_REGISTRATION_CODE": "invite-only"},
            clear=True,
        ):
            principal = register_web_user("alice", "alice-password", "invite-only")
        with self.sessions() as db:
            account = db.get(WebUser, principal.user_id)
            self.assertNotIn("alice-password", account.password_hash)
            self.assertTrue(verify_password("alice-password", account.password_hash))
            self.assertFalse(verify_password("wrong-password", account.password_hash))

    def test_legacy_environment_account_is_migrated_on_first_successful_login(self):
        with patch.dict(
            os.environ,
            {
                "ASHARE_WEB_USERNAME": "owner",
                "ASHARE_WEB_PASSWORD": "legacy-password",
            },
            clear=True,
        ), patch("web_auth._legacy_data_user_id", return_value=0):
            self.assertIsNone(authenticate_web_user("owner", "wrong-password"))
            principal = authenticate_web_user("owner", "legacy-password")

        self.assertEqual(principal.user_id, 0)
        self.assertTrue(principal.legacy_env)
        with self.sessions() as db:
            self.assertEqual(db.query(WebUser).count(), 1)


if __name__ == "__main__":
    unittest.main()

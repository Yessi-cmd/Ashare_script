import os
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import (
    AlertState,
    Base,
    PaperAccount,
    PaperOrder,
    PaperPosition,
    Portfolio,
    User,
    Watchlist,
    WebUser,
)
from web_auth import (
    WebAuthError,
    admin_create_web_user,
    authenticate_web_user,
    delete_web_user,
    hash_password,
    list_web_users,
    load_web_principal,
    make_signed_token,
    read_signed_token,
    register_web_user,
    reset_web_user_password,
    set_web_user_active,
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

    def test_admin_account_lifecycle_invalidates_sessions_and_resets_password(self):
        with patch.dict(
            os.environ,
            {
                "ASHARE_WEB_REGISTRATION_CODE": "invite-only",
                "ASHARE_WEB_ADMIN_USERNAME": "owner",
            },
            clear=True,
        ):
            owner = register_web_user("owner", "owner-password", "invite-only")
            bob = admin_create_web_user("bob", "bob-password")

            self.assertTrue(owner.is_admin)
            self.assertFalse(bob.is_admin)
            self.assertEqual(
                {row["username"] for row in list_web_users()},
                {"owner", "bob"},
            )

            set_web_user_active(owner.user_id, bob.user_id, False)
            self.assertIsNone(authenticate_web_user("bob", "bob-password"))
            self.assertIsNone(load_web_principal(bob.user_id, "bob"))

            set_web_user_active(owner.user_id, bob.user_id, True)
            reset_web_user_password(bob.user_id, "new-bob-password")
            self.assertIsNone(authenticate_web_user("bob", "bob-password"))
            self.assertIsNotNone(authenticate_web_user("bob", "new-bob-password"))

            with self.assertRaisesRegex(WebAuthError, "不能停用"):
                set_web_user_active(owner.user_id, owner.user_id, False)
            with self.assertRaisesRegex(WebAuthError, "不能删除"):
                delete_web_user(owner.user_id, owner.user_id)

    def test_admin_delete_removes_private_data_but_keeps_other_users(self):
        with patch.dict(
            os.environ,
            {
                "ASHARE_WEB_REGISTRATION_CODE": "invite-only",
                "ASHARE_WEB_ADMIN_USERNAME": "owner",
            },
            clear=True,
        ):
            owner = register_web_user("owner", "owner-password", "invite-only")
            bob = admin_create_web_user("bob", "bob-password")

        with self.sessions() as db:
            account = PaperAccount(owner_user_id=bob.user_id)
            db.add_all([
                Portfolio(
                    user_id=bob.user_id,
                    stock_code="600519",
                    name="贵州茅台",
                    buy_price=1500,
                    shares=100,
                ),
                Watchlist(
                    user_id=bob.user_id,
                    stock_code="300750",
                    name="宁德时代",
                ),
                account,
                AlertState(
                    owner_user_id=bob.user_id,
                    stock_code="600519",
                    alert_type="stop-loss",
                    last_alerted_at=1.0,
                ),
            ])
            db.flush()
            db.add_all([
                PaperPosition(
                    account=account,
                    stock_code="600519",
                    name="贵州茅台",
                    shares=100,
                    cost_basis_fen=15000000,
                ),
                PaperOrder(
                    account=account,
                    client_order_id="delete-test-order",
                    side="BUY",
                    stock_code="600519",
                    name="贵州茅台",
                    quantity=100,
                ),
            ])
            db.commit()

        delete_web_user(owner.user_id, bob.user_id)

        with self.sessions() as db:
            self.assertIsNotNone(db.get(User, owner.user_id))
            self.assertIsNone(db.get(User, bob.user_id))
            self.assertIsNone(db.get(WebUser, bob.user_id))
            self.assertEqual(db.query(Portfolio).filter(
                Portfolio.user_id == bob.user_id
            ).count(), 0)
            self.assertEqual(db.query(Watchlist).filter(
                Watchlist.user_id == bob.user_id
            ).count(), 0)
            self.assertEqual(db.query(PaperAccount).filter(
                PaperAccount.owner_user_id == bob.user_id
            ).count(), 0)
            self.assertEqual(db.query(PaperPosition).filter(
                PaperPosition.owner_user_id == bob.user_id
            ).count(), 0)
            self.assertEqual(db.query(PaperOrder).filter(
                PaperOrder.owner_user_id == bob.user_id
            ).count(), 0)
            self.assertEqual(db.query(AlertState).filter(
                AlertState.owner_user_id == bob.user_id
            ).count(), 0)

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
        self.assertTrue(principal.is_admin)
        with self.sessions() as db:
            self.assertEqual(db.query(WebUser).count(), 1)

    def test_existing_legacy_account_is_promoted_to_admin(self):
        with self.sessions() as db:
            db.add(User(user_id=0, username="owner"))
            db.add(WebUser(
                user_id=0,
                login_username="owner",
                password_hash=hash_password("legacy-password"),
                legacy_env=True,
                is_admin=False,
                is_active=True,
            ))
            db.commit()
        with patch.dict(
            os.environ,
            {
                "ASHARE_WEB_USERNAME": "owner",
                "ASHARE_WEB_PASSWORD": "legacy-password",
            },
            clear=True,
        ):
            principal = authenticate_web_user("owner", "legacy-password")
        self.assertTrue(principal.is_admin)


if __name__ == "__main__":
    unittest.main()

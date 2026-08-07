import unittest
from datetime import datetime
from unittest.mock import patch

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import (
    Base,
    PaperAccount,
    PaperOrder,
    PaperPosition,
    Portfolio,
    QuoteSnapshot,
    User,
)
from paper_trading import (
    PaperTradingError,
    calculate_trade_fee_fen,
    cancel_paper_order,
    ensure_paper_account,
    load_all_paper_monitoring_universe,
    load_pending_paper_owner_ids,
    load_paper_dashboard,
    load_paper_monitoring_universe,
    process_pending_paper_orders,
    submit_paper_order,
)


OPEN_CONFIG = {
    "monitor": {
        "interval_seconds": 30,
        "trading_hours": {
            "morning_start": "09:15",
            "morning_end": "11:30",
            "afternoon_start": "13:00",
            "afternoon_end": "15:00",
        },
    },
    "signal": {"buy_threshold": 70, "sell_threshold": 30},
}
THURSDAY_OPEN = datetime(2026, 7, 30, 10, 0)
FRIDAY_OPEN = datetime(2026, 7, 31, 10, 0)


def quote(code="600519", name="贵州茅台", price=10.0):
    return pd.DataFrame([{
        "代码": code,
        "名称": name,
        "最新价": price,
        "涨跌幅": 1.0,
        "成交量": 1_000,
    }])


class PaperTradingTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine)
        self.get_db_patch = patch(
            "paper_trading.get_db", side_effect=self.sessions
        )
        self.init_patch = patch("paper_trading.init_db")
        self.get_db_patch.start()
        self.init_patch.start()

    def tearDown(self):
        self.init_patch.stop()
        self.get_db_patch.stop()
        self.engine.dispose()

    def test_account_is_initialized_once_with_ten_thousand_yuan(self):
        first = ensure_paper_account(7)
        second = ensure_paper_account(7)

        self.assertEqual(first.cash_fen, 1_000_000)
        self.assertEqual(second.initial_cash_fen, 1_000_000)
        with self.sessions() as db:
            self.assertEqual(db.query(PaperAccount).count(), 1)

    def test_order_is_normalized_lot_validated_and_idempotent(self):
        first = submit_paper_order(
            7, "buy", "1", 100, "order_key_0001", submitted_at=THURSDAY_OPEN
        )
        duplicate = submit_paper_order(
            7, "BUY", "000001", 100, "order_key_0001", submitted_at=THURSDAY_OPEN
        )

        self.assertEqual(first.id, duplicate.id)
        self.assertEqual(first.stock_code, "000001")
        with self.sessions() as db:
            self.assertEqual(db.query(PaperOrder).count(), 1)

        with self.assertRaises(PaperTradingError) as conflict:
            submit_paper_order(
                7,
                "BUY",
                "600519",
                100,
                "order_key_0001",
                submitted_at=THURSDAY_OPEN,
            )
        self.assertEqual(conflict.exception.code, "duplicate-order-conflict")

        with self.assertRaises(PaperTradingError) as raised:
            submit_paper_order(
                7,
                "BUY",
                "600519",
                50,
                "order_key_0002",
                submitted_at=THURSDAY_OPEN,
            )
        self.assertEqual(raised.exception.code, "invalid-quantity")

    def test_pending_symbol_is_monitored_until_cancelled(self):
        order = submit_paper_order(
            7,
            "BUY",
            "300750",
            100,
            "order_key_0003",
            submitted_at=THURSDAY_OPEN,
        )
        self.assertEqual(load_paper_monitoring_universe(7), {"300750": "300750"})

        cancel_paper_order(7, order.id)
        self.assertEqual(load_paper_monitoring_universe(7), {})

    def test_multiple_paper_users_keep_orders_and_quotes_isolated(self):
        submit_paper_order(
            7, "BUY", "600519", 100, "order_key_multi_7", submitted_at=THURSDAY_OPEN
        )
        submit_paper_order(
            8, "BUY", "300750", 100, "order_key_multi_8", submitted_at=THURSDAY_OPEN
        )
        self.assertEqual(
            load_all_paper_monitoring_universe(),
            {"600519": "600519", "300750": "300750"},
        )
        self.assertEqual(load_pending_paper_owner_ids(), [7, 8])

        process_pending_paper_orders(
            quote("600519", price=10.0), 7, OPEN_CONFIG, executed_at=THURSDAY_OPEN
        )
        with self.sessions() as db:
            self.assertEqual(db.query(PaperPosition).filter(
                PaperPosition.owner_user_id == 7
            ).count(), 1)
            self.assertEqual(db.query(PaperPosition).filter(
                PaperPosition.owner_user_id == 8
            ).count(), 0)
            self.assertEqual(db.query(PaperOrder).filter(
                PaperOrder.owner_user_id == 8,
                PaperOrder.status == "pending",
            ).count(), 1)

        process_pending_paper_orders(
            quote("300750", price=20.0), 8, OPEN_CONFIG, executed_at=THURSDAY_OPEN
        )
        with self.sessions() as db:
            self.assertEqual(db.query(PaperPosition).filter(
                PaperPosition.owner_user_id == 8
            ).count(), 1)

    def test_buy_uses_integer_fen_and_includes_minimum_commission(self):
        submit_paper_order(
            7,
            "BUY",
            "600519",
            100,
            "order_key_0004",
            submitted_at=THURSDAY_OPEN,
        )
        result = process_pending_paper_orders(
            quote(), 7, OPEN_CONFIG, executed_at=THURSDAY_OPEN
        )

        self.assertEqual(result, {"filled": 1, "rejected": 0})
        self.assertEqual(calculate_trade_fee_fen("BUY", 100_000), 501)
        with self.sessions() as db:
            account = db.get(PaperAccount, 7)
            position = db.query(PaperPosition).one()
            order = db.query(PaperOrder).one()
            self.assertEqual(account.cash_fen, 899_499)
            self.assertEqual(position.shares, 100)
            self.assertEqual(position.cost_basis_fen, 100_501)
            self.assertEqual(order.price_fen, 1_000)
            self.assertEqual(order.fee_fen, 501)

    def test_buy_then_next_day_sell_enforces_t_plus_one_and_realizes_profit(self):
        submit_paper_order(
            7,
            "BUY",
            "600519",
            100,
            "order_key_0005",
            submitted_at=THURSDAY_OPEN,
        )
        process_pending_paper_orders(
            quote(), 7, OPEN_CONFIG, executed_at=THURSDAY_OPEN
        )

        with self.assertRaises(PaperTradingError) as raised:
            submit_paper_order(
                7,
                "SELL",
                "600519",
                100,
                "order_key_0006",
                submitted_at=THURSDAY_OPEN,
            )
        self.assertEqual(raised.exception.code, "insufficient-shares")

        submit_paper_order(
            7,
            "SELL",
            "600519",
            100,
            "order_key_0007",
            submitted_at=FRIDAY_OPEN,
        )
        with self.assertRaises(PaperTradingError) as reserved:
            submit_paper_order(
                7,
                "SELL",
                "600519",
                100,
                "order_key_0007_extra",
                submitted_at=FRIDAY_OPEN,
            )
        self.assertEqual(reserved.exception.code, "insufficient-shares")
        result = process_pending_paper_orders(
            quote(price=11.0), 7, OPEN_CONFIG, executed_at=FRIDAY_OPEN
        )

        self.assertEqual(result, {"filled": 1, "rejected": 0})
        self.assertEqual(calculate_trade_fee_fen("SELL", 110_000), 557)
        with self.sessions() as db:
            account = db.get(PaperAccount, 7)
            self.assertEqual(account.cash_fen, 1_008_942)
            self.assertEqual(account.realized_pnl_fen, 8_942)
            self.assertEqual(db.query(PaperPosition).count(), 0)

    def test_fifo_rejects_later_buy_when_cash_is_exhausted(self):
        for suffix in ("a", "b"):
            submit_paper_order(
                7,
                "BUY",
                "600519",
                100,
                f"order_key_0008_{suffix}",
                submitted_at=THURSDAY_OPEN,
            )
        result = process_pending_paper_orders(
            quote(price=60.0), 7, OPEN_CONFIG, executed_at=THURSDAY_OPEN
        )

        self.assertEqual(result, {"filled": 1, "rejected": 1})
        with self.sessions() as db:
            statuses = [
                (row.status, row.reject_reason)
                for row in db.query(PaperOrder).order_by(PaperOrder.id)
            ]
        self.assertEqual(statuses[0], ("filled", None))
        self.assertEqual(statuses[1], ("rejected", "可用资金不足"))

    def test_closed_market_keeps_order_pending(self):
        saturday = datetime(2026, 8, 1, 10, 0)
        submit_paper_order(
            7,
            "BUY",
            "600519",
            100,
            "order_key_0009",
            submitted_at=saturday,
        )
        result = process_pending_paper_orders(
            quote(), 7, OPEN_CONFIG, executed_at=saturday
        )

        self.assertEqual(result, {"filled": 0, "rejected": 0})
        with self.sessions() as db:
            self.assertEqual(db.query(PaperOrder).one().status, "pending")

    def test_dashboard_values_paper_and_manual_live_portfolios_from_snapshots(self):
        submit_paper_order(
            7,
            "BUY",
            "600519",
            100,
            "order_key_0010",
            submitted_at=THURSDAY_OPEN,
        )
        process_pending_paper_orders(
            quote(), 7, OPEN_CONFIG, executed_at=THURSDAY_OPEN
        )
        with self.sessions() as db:
            db.add(User(user_id=7, username="owner"))
            db.add(Portfolio(
                user_id=7,
                stock_code="600519",
                name="贵州茅台",
                buy_price=9.0,
                shares=100,
                stop_loss=-5,
                take_profit=10,
            ))
            db.add(QuoteSnapshot(
                stock_code="600519",
                name="贵州茅台",
                price=11.0,
                change_pct=2.0,
                score=75,
                reason="趋势偏强",
                quote_at=FRIDAY_OPEN,
            ))
            db.commit()

        data = load_paper_dashboard(
            {**OPEN_CONFIG, "app": {"owner_user_id": 7}}, at=FRIDAY_OPEN
        )

        self.assertAlmostEqual(data["account"]["cash"], 8_994.99)
        self.assertAlmostEqual(data["account"]["market_value"], 1_100.0)
        self.assertAlmostEqual(data["account"]["equity"], 10_094.99)
        self.assertEqual(data["positions"][0]["sellable_shares"], 100)
        self.assertEqual(data["positions"][0]["signal_label"], "偏强")
        self.assertAlmostEqual(data["live"]["total_profit"], 200.0)


if __name__ == "__main__":
    unittest.main()

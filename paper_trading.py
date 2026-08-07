"""Local paper-trading ledger and monitor-driven market-order execution."""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, time
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import func

from database import (
    PaperAccount,
    PaperOrder,
    PaperPosition,
    Portfolio,
    QuoteSnapshot,
    get_db,
    init_db,
)
from holidays import is_trading_day
from market_data import normalize_stock_code
from settings import get_owner_user_id

logger = logging.getLogger(__name__)

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
INITIAL_CASH_FEN = 1_000_000
LOT_SIZE = 100
COMMISSION_RATE = Decimal("0.0003")
MIN_COMMISSION_FEN = 500
TRANSFER_FEE_RATE = Decimal("0.00001")
STAMP_DUTY_RATE = Decimal("0.0005")
DEFAULT_STOP_LOSS_PCT = -5.0
DEFAULT_TAKE_PROFIT_PCT = 10.0
CLIENT_ORDER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

ORDER_STATUS_LABELS = {
    "pending": "待成交",
    "filled": "已成交",
    "rejected": "已拒绝",
    "cancelled": "已撤销",
}
SIDE_LABELS = {"BUY": "买入", "SELL": "卖出"}


class PaperTradingError(ValueError):
    """A user-correctable paper-trading validation error."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def paper_owner_user_id(config: dict, user_id: int | None = None) -> int:
    """Resolve an explicit web user or the legacy configured owner key."""
    if user_id is not None:
        return int(user_id)
    return get_owner_user_id(config) or 0


def _now_shanghai_naive() -> datetime:
    return datetime.now(SHANGHAI_TZ).replace(tzinfo=None)


def _normalize_local_time(value: datetime | None) -> datetime:
    if value is None:
        return _now_shanghai_naive()
    if value.tzinfo is not None:
        return value.astimezone(SHANGHAI_TZ).replace(tzinfo=None)
    return value


def _parse_clock(value: str, fallback: time) -> time:
    try:
        return datetime.strptime(value, "%H:%M").time()
    except (TypeError, ValueError):
        return fallback


def paper_market_is_open(config: dict, at: datetime | None = None) -> bool:
    """Use the monitor's configured A-share sessions for execution gating."""
    local_at = _normalize_local_time(at)
    if not is_trading_day(local_at.date()):
        return False
    trading = config.get("monitor", {}).get("trading_hours", {})
    morning_start = _parse_clock(trading.get("morning_start", "09:15"), time(9, 15))
    morning_end = _parse_clock(trading.get("morning_end", "11:30"), time(11, 30))
    afternoon_start = _parse_clock(
        trading.get("afternoon_start", "13:00"), time(13, 0)
    )
    afternoon_end = _parse_clock(trading.get("afternoon_end", "15:00"), time(15, 0))
    clock = local_at.time()
    return morning_start <= clock <= morning_end or afternoon_start <= clock <= afternoon_end


def _ensure_account(db, owner_user_id: int) -> PaperAccount:
    account = db.get(PaperAccount, owner_user_id)
    if account is None:
        account = PaperAccount(
            owner_user_id=owner_user_id,
            initial_cash_fen=INITIAL_CASH_FEN,
            cash_fen=INITIAL_CASH_FEN,
            realized_pnl_fen=0,
        )
        db.add(account)
        db.flush()
    return account


def ensure_paper_account(owner_user_id: int) -> PaperAccount:
    """Provision one user's fixed 10,000-yuan paper account idempotently."""
    init_db()
    db = get_db()
    try:
        account = _ensure_account(db, owner_user_id)
        db.commit()
        db.refresh(account)
        return account
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min)
    return start, datetime.combine(day, time.max)


def _bought_today(db, owner_user_id: int, stock_code: str, day: date) -> int:
    start, end = _day_bounds(day)
    return int(
        db.query(func.coalesce(func.sum(PaperOrder.quantity), 0))
        .filter(
            PaperOrder.owner_user_id == owner_user_id,
            PaperOrder.stock_code == stock_code,
            PaperOrder.side == "BUY",
            PaperOrder.status == "filled",
            PaperOrder.executed_at >= start,
            PaperOrder.executed_at <= end,
        )
        .scalar()
        or 0
    )


def _pending_sell_quantity(db, owner_user_id: int, stock_code: str) -> int:
    return int(
        db.query(func.coalesce(func.sum(PaperOrder.quantity), 0))
        .filter(
            PaperOrder.owner_user_id == owner_user_id,
            PaperOrder.stock_code == stock_code,
            PaperOrder.side == "SELL",
            PaperOrder.status == "pending",
        )
        .scalar()
        or 0
    )


def _sellable_shares(
    db,
    position: PaperPosition,
    day: date,
    *,
    reserve_pending: bool,
) -> int:
    unavailable = _bought_today(
        db, position.owner_user_id, position.stock_code, day
    )
    if reserve_pending:
        unavailable += _pending_sell_quantity(
            db, position.owner_user_id, position.stock_code
        )
    return max(0, position.shares - unavailable)


def submit_paper_order(
    owner_user_id: int,
    side: str,
    stock_code: str,
    quantity: int,
    client_order_id: str,
    *,
    submitted_at: datetime | None = None,
) -> PaperOrder:
    """Validate and enqueue an idempotent paper market order."""
    normalized_side = str(side).strip().upper()
    if normalized_side not in SIDE_LABELS:
        raise PaperTradingError("invalid-side", "委托方向无效")
    try:
        code = normalize_stock_code(stock_code)
    except ValueError as exc:
        raise PaperTradingError("invalid-code", str(exc)) from exc
    try:
        shares = int(quantity)
    except (TypeError, ValueError) as exc:
        raise PaperTradingError("invalid-quantity", "委托数量必须是整数") from exc
    if shares <= 0 or shares % LOT_SIZE != 0:
        raise PaperTradingError(
            "invalid-quantity", f"委托数量必须是 {LOT_SIZE} 股的正整数倍"
        )
    client_id = str(client_order_id)
    if not CLIENT_ORDER_ID_PATTERN.fullmatch(client_id):
        raise PaperTradingError("invalid-order-id", "客户端委托号无效")

    now = _normalize_local_time(submitted_at)
    init_db()
    db = get_db()
    try:
        _ensure_account(db, owner_user_id)
        existing = db.query(PaperOrder).filter(
            PaperOrder.owner_user_id == owner_user_id,
            PaperOrder.client_order_id == client_id,
        ).first()
        if existing is not None:
            if (
                existing.side != normalized_side
                or existing.stock_code != code
                or existing.quantity != shares
            ):
                raise PaperTradingError(
                    "duplicate-order-conflict", "客户端委托号已用于其他委托"
                )
            return existing

        name = code
        snapshot = db.get(QuoteSnapshot, code)
        if snapshot is not None:
            name = snapshot.name

        if normalized_side == "SELL":
            position = db.query(PaperPosition).filter(
                PaperPosition.owner_user_id == owner_user_id,
                PaperPosition.stock_code == code,
            ).first()
            if position is None:
                raise PaperTradingError("no-position", "没有可卖出的模拟持仓")
            sellable = _sellable_shares(
                db, position, now.date(), reserve_pending=True
            )
            if shares > sellable:
                raise PaperTradingError(
                    "insufficient-shares", f"当前最多可委托卖出 {sellable} 股"
                )

        order = PaperOrder(
            owner_user_id=owner_user_id,
            client_order_id=client_id,
            side=normalized_side,
            stock_code=code,
            name=name,
            quantity=shares,
            status="pending",
            submitted_at=now,
        )
        db.add(order)
        db.commit()
        db.refresh(order)
        return order
    except PaperTradingError:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def cancel_paper_order(owner_user_id: int, order_id: int) -> PaperOrder:
    """Cancel a pending order owned by the configured paper account."""
    init_db()
    db = get_db()
    try:
        order = db.query(PaperOrder).filter(
            PaperOrder.id == order_id,
            PaperOrder.owner_user_id == owner_user_id,
        ).first()
        if order is None:
            raise PaperTradingError("order-not-found", "委托不存在")
        if order.status != "pending":
            raise PaperTradingError("order-not-pending", "只有待成交委托可以撤销")
        order.status = "cancelled"
        db.commit()
        db.refresh(order)
        return order
    except PaperTradingError:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def load_paper_monitoring_universe(owner_user_id: int) -> dict[str, str]:
    """Return held and pending symbols that must receive realtime quotes."""
    init_db()
    db = get_db()
    try:
        names = {
            row.stock_code: row.name
            for row in db.query(PaperPosition).filter(
                PaperPosition.owner_user_id == owner_user_id
            ).all()
        }
        for order in db.query(PaperOrder).filter(
            PaperOrder.owner_user_id == owner_user_id,
            PaperOrder.status == "pending",
        ).all():
            names.setdefault(order.stock_code, order.name or order.stock_code)
        return names
    finally:
        db.close()


def load_all_paper_monitoring_universe() -> dict[str, str]:
    """Return the deduplicated paper symbols needed by every paper account."""
    init_db()
    db = get_db()
    try:
        names = {
            row.stock_code: row.name
            for row in db.query(PaperPosition).all()
        }
        for order in db.query(PaperOrder).filter(
            PaperOrder.status == "pending"
        ).all():
            names.setdefault(order.stock_code, order.name or order.stock_code)
        return names
    finally:
        db.close()


def load_pending_paper_owner_ids() -> list[int]:
    """Return owners with pending orders for the multi-user monitor."""
    init_db()
    db = get_db()
    try:
        rows = db.query(PaperOrder.owner_user_id).filter(
            PaperOrder.status == "pending"
        ).distinct().all()
        return [int(owner_user_id) for (owner_user_id,) in rows]
    finally:
        db.close()


def _price_to_fen(value) -> int | None:
    try:
        price = Decimal(str(value))
    except Exception:
        return None
    if not price.is_finite() or price <= 0:
        return None
    return int((price * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _ceil_rate(gross_amount_fen: int, rate: Decimal) -> int:
    return int(
        (Decimal(gross_amount_fen) * rate).quantize(
            Decimal("1"), rounding=ROUND_CEILING
        )
    )


def calculate_trade_fee_fen(side: str, gross_amount_fen: int) -> int:
    """Calculate broker-like A-share fees at one-fen precision."""
    commission = max(
        MIN_COMMISSION_FEN, _ceil_rate(gross_amount_fen, COMMISSION_RATE)
    )
    transfer_fee = _ceil_rate(gross_amount_fen, TRANSFER_FEE_RATE)
    stamp_duty = (
        _ceil_rate(gross_amount_fen, STAMP_DUTY_RATE)
        if side == "SELL"
        else 0
    )
    return commission + transfer_fee + stamp_duty


def _quote_map(quotes_df: pd.DataFrame | None) -> dict[str, dict]:
    if quotes_df is None or quotes_df.empty:
        return {}
    quotes = {}
    for _, row in quotes_df.iterrows():
        code = str(row.get("代码", "")).zfill(6)
        price_fen = _price_to_fen(row.get("最新价"))
        if len(code) != 6 or not code.isdigit() or price_fen is None:
            continue
        raw_name = row.get("名称")
        name = code if raw_name is None or pd.isna(raw_name) else str(raw_name)
        quotes[code] = {
            "name": name[:50],
            "price_fen": price_fen,
        }
    return quotes


def _reject_order(order: PaperOrder, reason: str, executed_at: datetime) -> None:
    order.status = "rejected"
    order.reject_reason = reason
    order.executed_at = executed_at


def process_pending_paper_orders(
    quotes_df: pd.DataFrame | None,
    owner_user_id: int,
    config: dict,
    *,
    executed_at: datetime | None = None,
) -> dict[str, int]:
    """Fill matching pending orders in FIFO order during an open market session."""
    now = _normalize_local_time(executed_at)
    quotes = _quote_map(quotes_df)
    if not quotes or not paper_market_is_open(config, now):
        return {"filled": 0, "rejected": 0}

    init_db()
    db = get_db()
    filled = 0
    rejected = 0
    try:
        account = _ensure_account(db, owner_user_id)
        orders = db.query(PaperOrder).filter(
            PaperOrder.owner_user_id == owner_user_id,
            PaperOrder.status == "pending",
            PaperOrder.stock_code.in_(quotes),
        ).order_by(PaperOrder.id).all()

        for order in orders:
            quote = quotes[order.stock_code]
            price_fen = quote["price_fen"]
            gross = price_fen * order.quantity
            fee = calculate_trade_fee_fen(order.side, gross)
            order.name = quote["name"]

            if order.side == "BUY":
                required_cash = gross + fee
                if required_cash > account.cash_fen:
                    _reject_order(order, "可用资金不足", now)
                    rejected += 1
                    continue
                account.cash_fen -= required_cash
                position = db.query(PaperPosition).filter(
                    PaperPosition.owner_user_id == owner_user_id,
                    PaperPosition.stock_code == order.stock_code,
                ).first()
                if position is None:
                    position = PaperPosition(
                        owner_user_id=owner_user_id,
                        stock_code=order.stock_code,
                        name=order.name,
                        shares=0,
                        cost_basis_fen=0,
                    )
                    db.add(position)
                position.name = order.name
                position.shares += order.quantity
                position.cost_basis_fen += required_cash
            else:
                position = db.query(PaperPosition).filter(
                    PaperPosition.owner_user_id == owner_user_id,
                    PaperPosition.stock_code == order.stock_code,
                ).first()
                if position is None:
                    _reject_order(order, "模拟持仓不存在", now)
                    rejected += 1
                    continue
                sellable = _sellable_shares(
                    db, position, now.date(), reserve_pending=False
                )
                if order.quantity > sellable:
                    _reject_order(order, "T+1 可卖数量不足", now)
                    rejected += 1
                    continue
                proceeds = gross - fee
                if proceeds <= 0:
                    _reject_order(order, "成交金额不足以覆盖交易费用", now)
                    rejected += 1
                    continue
                if order.quantity == position.shares:
                    released_cost = position.cost_basis_fen
                else:
                    released_cost = int(
                        (
                            Decimal(position.cost_basis_fen)
                            * Decimal(order.quantity)
                            / Decimal(position.shares)
                        ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
                    )
                account.cash_fen += proceeds
                account.realized_pnl_fen += proceeds - released_cost
                position.shares -= order.quantity
                position.cost_basis_fen -= released_cost
                if position.shares == 0:
                    db.delete(position)

            order.status = "filled"
            order.executed_at = now
            order.price_fen = price_fen
            order.gross_amount_fen = gross
            order.fee_fen = fee
            order.reject_reason = None
            filled += 1

        db.commit()
        if filled or rejected:
            logger.info(
                f"模拟盘撮合完成: 成交 {filled} 笔，拒绝 {rejected} 笔"
            )
        return {"filled": filled, "rejected": rejected}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _yuan(fen: int | None) -> float | None:
    return None if fen is None else fen / 100.0


def _live_portfolio_rows(
    db,
    config: dict,
    owner_user_id: int,
    *,
    explicit_user_id: int | None = None,
) -> list[dict]:
    configured_owner = get_owner_user_id(config)
    data_user_id = explicit_user_id if explicit_user_id is not None else configured_owner
    if data_user_id is not None:
        return [
            {
                "code": row.stock_code,
                "name": row.name,
                "buy_price": float(row.buy_price),
                "shares": int(row.shares),
            }
            for row in db.query(Portfolio).filter(
                Portfolio.user_id == data_user_id
            ).all()
        ]
    return [
        {
            "code": code,
            "name": holding.get("name", code),
            "buy_price": float(holding.get("buy_price", 0)),
            "shares": int(holding.get("shares", 0)),
        }
        for code, holding in (config.get("portfolio", {}) or {}).items()
    ]


def load_paper_dashboard(
    config: dict,
    *,
    user_id: int | None = None,
    at: datetime | None = None,
) -> dict:
    """Load one user's paper account and manual live-portfolio comparison."""
    owner_user_id = paper_owner_user_id(config, user_id)
    now = _normalize_local_time(at)
    interval = float(config.get("monitor", {}).get("interval_seconds", 30))
    stale_after = max(120.0, interval * 3)
    init_db()
    db = get_db()
    try:
        account = _ensure_account(db, owner_user_id)
        db.commit()
        positions = db.query(PaperPosition).filter(
            PaperPosition.owner_user_id == owner_user_id
        ).order_by(PaperPosition.stock_code).all()
        orders = db.query(PaperOrder).filter(
            PaperOrder.owner_user_id == owner_user_id
        ).order_by(PaperOrder.id.desc()).limit(50).all()
        pending_count = int(
            db.query(func.count(PaperOrder.id)).filter(
                PaperOrder.owner_user_id == owner_user_id,
                PaperOrder.status == "pending",
            ).scalar()
            or 0
        )
        live_rows = _live_portfolio_rows(
            db,
            config,
            owner_user_id,
            explicit_user_id=user_id,
        )
        codes = {row.stock_code for row in positions}
        codes.update(row["code"] for row in live_rows)
        snapshots = {
            row.stock_code: row
            for row in db.query(QuoteSnapshot).filter(
                QuoteSnapshot.stock_code.in_(codes)
            ).all()
        } if codes else {}

        paper_rows = []
        market_value_fen = 0
        latest_quote_at = None
        buy_threshold = int(config.get("signal", {}).get("buy_threshold", 70))
        sell_threshold = int(config.get("signal", {}).get("sell_threshold", 30))
        for position in positions:
            snapshot = snapshots.get(position.stock_code)
            quote_price_fen = _price_to_fen(snapshot.price) if snapshot else None
            estimated_price_fen = quote_price_fen or int(
                (
                    Decimal(position.cost_basis_fen) / Decimal(position.shares)
                ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            )
            value_fen = estimated_price_fen * position.shares
            market_value_fen += value_fen
            profit_fen = value_fen - position.cost_basis_fen
            profit_pct = (
                profit_fen / position.cost_basis_fen * 100
                if position.cost_basis_fen
                else 0.0
            )
            score = snapshot.score if snapshot else None
            if score is not None and score >= buy_threshold:
                signal_label = "偏强"
            elif score is not None and score <= sell_threshold:
                signal_label = "风险"
            else:
                signal_label = "观望"
            sellable = _sellable_shares(
                db, position, now.date(), reserve_pending=True
            )
            if snapshot and (latest_quote_at is None or snapshot.quote_at > latest_quote_at):
                latest_quote_at = snapshot.quote_at
            paper_rows.append({
                "code": position.stock_code,
                "name": position.name,
                "shares": position.shares,
                "sellable_shares": sellable,
                "average_cost": position.cost_basis_fen / position.shares / 100,
                "cost_basis": _yuan(position.cost_basis_fen),
                "market_value": _yuan(value_fen),
                "profit_amount": _yuan(profit_fen),
                "profit_pct": profit_pct,
                "stop_price": position.cost_basis_fen / position.shares / 100
                * (1 + DEFAULT_STOP_LOSS_PCT / 100),
                "take_profit_price": position.cost_basis_fen / position.shares / 100
                * (1 + DEFAULT_TAKE_PROFIT_PCT / 100),
                "snapshot": {
                    "price": snapshot.price,
                    "change_pct": snapshot.change_pct,
                    "score": snapshot.score,
                    "reason": snapshot.reason or "暂无评分说明",
                    "quote_at": snapshot.quote_at,
                    "stale": max(0.0, (now - snapshot.quote_at).total_seconds())
                    > stale_after,
                } if snapshot else None,
                "signal_label": signal_label,
            })

        equity_fen = account.cash_fen + market_value_fen
        total_profit_fen = equity_fen - account.initial_cash_fen

        live_cost = 0.0
        live_value = 0.0
        live_priced_count = 0
        for row in live_rows:
            cost = row["buy_price"] * row["shares"]
            snapshot = snapshots.get(row["code"])
            value = snapshot.price * row["shares"] if snapshot else cost
            live_cost += cost
            live_value += value
            if snapshot:
                live_priced_count += 1

        order_rows = [{
            "id": order.id,
            "side": order.side,
            "side_label": SIDE_LABELS.get(order.side, order.side),
            "code": order.stock_code,
            "name": order.name,
            "quantity": order.quantity,
            "status": order.status,
            "status_label": ORDER_STATUS_LABELS.get(order.status, order.status),
            "submitted_at": order.submitted_at,
            "executed_at": order.executed_at,
            "price": _yuan(order.price_fen),
            "gross_amount": _yuan(order.gross_amount_fen),
            "fee": _yuan(order.fee_fen),
            "reject_reason": order.reject_reason,
        } for order in orders]

        return {
            "owner_user_id": owner_user_id,
            "account": {
                "initial_cash": _yuan(account.initial_cash_fen),
                "cash": _yuan(account.cash_fen),
                "market_value": _yuan(market_value_fen),
                "equity": _yuan(equity_fen),
                "total_profit": _yuan(total_profit_fen),
                "total_profit_pct": total_profit_fen / account.initial_cash_fen * 100,
                "realized_profit": _yuan(account.realized_pnl_fen),
            },
            "positions": paper_rows,
            "orders": order_rows,
            "pending_count": pending_count,
            "market_open": paper_market_is_open(config, now),
            "latest_quote_at": latest_quote_at,
            "stale_after_seconds": stale_after,
            "live": {
                "holding_count": len(live_rows),
                "priced_count": live_priced_count,
                "total_cost": live_cost,
                "total_value": live_value,
                "total_profit": live_value - live_cost,
                "total_profit_pct": (
                    (live_value / live_cost - 1) * 100 if live_cost else 0.0
                ),
            },
            "rules": {
                "lot_size": LOT_SIZE,
                "stop_loss_pct": DEFAULT_STOP_LOSS_PCT,
                "take_profit_pct": DEFAULT_TAKE_PROFIT_PCT,
            },
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

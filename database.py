#!/usr/bin/env python3
"""
数据库模型定义
使用 SQLAlchemy ORM
"""
import logging
import os
import threading
from datetime import datetime
from pathlib import Path

from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    create_engine,
    event,
)
from sqlalchemy.orm import Session, declarative_base, relationship, sessionmaker

logger = logging.getLogger(__name__)

# 数据库文件默认固定在项目目录，避免从不同工作目录启动时创建多份数据库。
DEFAULT_DB_PATH = Path(__file__).resolve().with_name("ashare_monitor.db")
DATABASE_URL = os.getenv("ASHARE_DATABASE_URL", f"sqlite:///{DEFAULT_DB_PATH}")

# 创建引擎
engine = create_engine(
    DATABASE_URL,
    echo=False,  # 设为 True 可以看到 SQL 语句
    connect_args={"check_same_thread": False}  # SQLite 多线程支持
)


@event.listens_for(engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record):
    """Enable SQLite foreign-key enforcement for every connection."""
    if DATABASE_URL.startswith("sqlite"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

# 创建 Session 工厂
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
_DB_INIT_LOCK = threading.Lock()
_DB_INITIALIZED = False

# 创建基类
Base = declarative_base()


# ── 数据库模型 ──────────────────────────────────────────────────


class User(Base):
    """用户表"""
    __tablename__ = "users"
    
    user_id = Column(Integer, primary_key=True, index=True)
    username = Column(String, nullable=True)  # 可选：用户名
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    
    # 关联关系
    portfolios = relationship("Portfolio", back_populates="user", cascade="all, delete-orphan")
    watchlist = relationship("Watchlist", back_populates="user", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<User(user_id={self.user_id}, username={self.username})>"


class Portfolio(Base):
    """持仓表"""
    __tablename__ = "portfolios"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.user_id"), nullable=False, index=True)
    stock_code = Column(String(10), nullable=False, index=True)
    name = Column(String(50), nullable=False)
    buy_price = Column(Float, nullable=False)
    shares = Column(Integer, nullable=False)
    stop_loss = Column(Float, default=-5.0)  # 止损百分比
    take_profit = Column(Float, default=10.0)  # 止盈百分比
    created_at = Column(DateTime, default=datetime.now)
    
    # 关联关系
    user = relationship("User", back_populates="portfolios")

    __table_args__ = (
        Index("uq_portfolios_user_stock", "user_id", "stock_code", unique=True),
    )
    
    def __repr__(self):
        return f"<Portfolio(user={self.user_id}, stock={self.stock_code}, name={self.name})>"


class Watchlist(Base):
    """关注池表"""
    __tablename__ = "watchlist"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.user_id"), nullable=False, index=True)
    stock_code = Column(String(10), nullable=False, index=True)
    name = Column(String(50), nullable=False)
    created_at = Column(DateTime, default=datetime.now)
    
    # 关联关系
    user = relationship("User", back_populates="watchlist")

    __table_args__ = (
        Index("uq_watchlist_user_stock", "user_id", "stock_code", unique=True),
    )
    
    def __repr__(self):
        return f"<Watchlist(user={self.user_id}, stock={self.stock_code}, name={self.name})>"


class AlertState(Base):
    """Persistent cooldown state for the personal monitor."""
    __tablename__ = "alert_states"

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_user_id = Column(Integer, nullable=False, default=0)
    stock_code = Column(String(10), nullable=False)
    alert_type = Column(String(30), nullable=False)
    last_alerted_at = Column(Float, nullable=False)

    __table_args__ = (
        Index(
            "uq_alert_state_owner_stock_type",
            "owner_user_id",
            "stock_code",
            "alert_type",
            unique=True,
        ),
    )


class DailyBar(Base):
    """Normalized daily A-share bar used by research and backtests."""
    __tablename__ = "daily_bars"

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_code = Column(String(10), nullable=False)
    trade_date = Column(Date, nullable=False)
    adjust = Column(String(10), nullable=False, default="qfq")
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Float, nullable=False)
    amount = Column(Float, nullable=True)
    source = Column(String(30), nullable=False, default="akshare")
    fetched_at = Column(DateTime, default=datetime.now, nullable=False)

    __table_args__ = (
        Index(
            "uq_daily_bars_code_date_adjust",
            "stock_code",
            "trade_date",
            "adjust",
            unique=True,
        ),
        Index("ix_daily_bars_code_date", "stock_code", "trade_date"),
    )


class QuoteSnapshot(Base):
    """Latest locally persisted realtime quote and live V2 score."""
    __tablename__ = "quote_snapshots"

    stock_code = Column(String(10), primary_key=True)
    name = Column(String(50), nullable=False)
    price = Column(Float, nullable=False)
    change_pct = Column(Float, nullable=False)
    volume = Column(Float, nullable=True)
    score = Column(Integer, nullable=True)
    reason = Column(String(500), nullable=True)
    quote_at = Column(DateTime, nullable=False, index=True)


class MarketQuoteSnapshot(Base):
    """Latest quote for a cross-market benchmark index."""
    __tablename__ = "market_quote_snapshots"

    market = Column(String(20), primary_key=True)
    symbol = Column(String(30), primary_key=True)
    name = Column(String(80), nullable=False)
    price = Column(Float, nullable=False)
    change_pct = Column(Float, nullable=False)
    currency = Column(String(10), nullable=False)
    quote_at = Column(DateTime, nullable=False, index=True)
    market_at = Column(DateTime, nullable=True)
    source = Column(String(40), nullable=False, default="yahoo_chart")


class PaperAccount(Base):
    """Single-owner paper-trading cash ledger, stored in integer fen."""
    __tablename__ = "paper_accounts"

    owner_user_id = Column(Integer, primary_key=True)
    initial_cash_fen = Column(Integer, nullable=False, default=1_000_000)
    cash_fen = Column(Integer, nullable=False, default=1_000_000)
    realized_pnl_fen = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.now, onupdate=datetime.now, nullable=False
    )

    positions = relationship(
        "PaperPosition", back_populates="account", cascade="all, delete-orphan"
    )
    orders = relationship(
        "PaperOrder", back_populates="account", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("initial_cash_fen > 0", name="ck_paper_account_initial_cash"),
        CheckConstraint("cash_fen >= 0", name="ck_paper_account_cash"),
    )


class PaperPosition(Base):
    """Current paper position with an all-in remaining cost basis."""
    __tablename__ = "paper_positions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_user_id = Column(
        Integer,
        ForeignKey("paper_accounts.owner_user_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stock_code = Column(String(10), nullable=False, index=True)
    name = Column(String(50), nullable=False)
    shares = Column(Integer, nullable=False)
    cost_basis_fen = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.now, onupdate=datetime.now, nullable=False
    )

    account = relationship("PaperAccount", back_populates="positions")

    __table_args__ = (
        Index(
            "uq_paper_positions_owner_stock",
            "owner_user_id",
            "stock_code",
            unique=True,
        ),
        CheckConstraint("shares > 0", name="ck_paper_position_shares"),
        CheckConstraint("cost_basis_fen >= 0", name="ck_paper_position_cost"),
    )


class PaperOrder(Base):
    """Immutable paper-order intent plus its eventual execution outcome."""
    __tablename__ = "paper_orders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_user_id = Column(
        Integer,
        ForeignKey("paper_accounts.owner_user_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    client_order_id = Column(String(64), nullable=False)
    side = Column(String(4), nullable=False)
    stock_code = Column(String(10), nullable=False, index=True)
    name = Column(String(50), nullable=False)
    quantity = Column(Integer, nullable=False)
    status = Column(String(12), nullable=False, default="pending", index=True)
    submitted_at = Column(DateTime, default=datetime.now, nullable=False)
    executed_at = Column(DateTime, nullable=True)
    price_fen = Column(Integer, nullable=True)
    gross_amount_fen = Column(Integer, nullable=True)
    fee_fen = Column(Integer, nullable=True)
    reject_reason = Column(String(200), nullable=True)

    account = relationship("PaperAccount", back_populates="orders")

    __table_args__ = (
        Index(
            "uq_paper_orders_owner_client",
            "owner_user_id",
            "client_order_id",
            unique=True,
        ),
        Index(
            "ix_paper_orders_owner_status_id",
            "owner_user_id",
            "status",
            "id",
        ),
        CheckConstraint("quantity > 0", name="ck_paper_order_quantity"),
        CheckConstraint("side IN ('BUY', 'SELL')", name="ck_paper_order_side"),
        CheckConstraint(
            "status IN ('pending', 'filled', 'rejected', 'cancelled')",
            name="ck_paper_order_status",
        ),
    )


# ── 数据库初始化 ──────────────────────────────────────────────────


def init_db():
    """初始化数据库（创建所有表）"""
    global _DB_INITIALIZED
    if _DB_INITIALIZED:
        return
    with _DB_INIT_LOCK:
        if _DB_INITIALIZED:
            return
        Base.metadata.create_all(bind=engine)
        # create_all 不会为既有表补建后加入的索引，因此显式检查创建。
        for table in (
            Portfolio.__table__,
            Watchlist.__table__,
            AlertState.__table__,
            DailyBar.__table__,
            QuoteSnapshot.__table__,
            MarketQuoteSnapshot.__table__,
            PaperAccount.__table__,
            PaperPosition.__table__,
            PaperOrder.__table__,
        ):
            for index in table.indexes:
                if index.unique:
                    index.create(bind=engine, checkfirst=True)
        _DB_INITIALIZED = True
        logger.info("数据库初始化完成")


def get_db() -> Session:
    """获取数据库会话"""
    return SessionLocal()


# ── ORM 辅助函数 ──────────────────────────────────────────────────


def get_or_create_user(db: Session, user_id: int, username: str = None) -> User:
    """获取或创建用户"""
    user = db.query(User).filter(User.user_id == user_id).first()
    if not user:
        user = User(user_id=user_id, username=username)
        db.add(user)
        db.commit()
        db.refresh(user)
        logger.info(f"创建新用户: {user_id}")
    return user


def add_portfolio(db: Session, user_id: int, stock_code: str, name: str, 
                  buy_price: float, shares: int, stop_loss: float = -5.0, 
                  take_profit: float = 10.0) -> Portfolio:
    """添加持仓"""
    # 检查是否已存在
    existing = db.query(Portfolio).filter(
        Portfolio.user_id == user_id,
        Portfolio.stock_code == stock_code
    ).first()
    
    if existing:
        # 更新现有持仓
        existing.name = name
        existing.buy_price = buy_price
        existing.shares = shares
        existing.stop_loss = stop_loss
        existing.take_profit = take_profit
        db.commit()
        db.refresh(existing)
        logger.info(f"更新持仓: {user_id} - {stock_code}")
        return existing
    else:
        # 创建新持仓
        portfolio = Portfolio(
            user_id=user_id,
            stock_code=stock_code,
            name=name,
            buy_price=buy_price,
            shares=shares,
            stop_loss=stop_loss,
            take_profit=take_profit
        )
        db.add(portfolio)
        db.commit()
        db.refresh(portfolio)
        logger.info(f"添加持仓: {user_id} - {stock_code}")
        return portfolio


def remove_portfolio(db: Session, user_id: int, stock_code: str) -> bool:
    """删除持仓"""
    portfolio = db.query(Portfolio).filter(
        Portfolio.user_id == user_id,
        Portfolio.stock_code == stock_code
    ).first()
    
    if portfolio:
        db.delete(portfolio)
        db.commit()
        logger.info(f"删除持仓: {user_id} - {stock_code}")
        return True
    return False


def add_watchlist(db: Session, user_id: int, stock_code: str, name: str) -> Watchlist:
    """添加关注"""
    # 检查是否已存在
    existing = db.query(Watchlist).filter(
        Watchlist.user_id == user_id,
        Watchlist.stock_code == stock_code
    ).first()
    
    if existing:
        existing.name = name
        db.commit()
        db.refresh(existing)
        return existing
    
    watchlist = Watchlist(user_id=user_id, stock_code=stock_code, name=name)
    db.add(watchlist)
    db.commit()
    db.refresh(watchlist)
    logger.info(f"添加关注: {user_id} - {stock_code}")
    return watchlist


def remove_watchlist(db: Session, user_id: int, stock_code: str) -> bool:
    """删除关注"""
    watchlist = db.query(Watchlist).filter(
        Watchlist.user_id == user_id,
        Watchlist.stock_code == stock_code
    ).first()
    
    if watchlist:
        db.delete(watchlist)
        db.commit()
        logger.info(f"删除关注: {user_id} - {stock_code}")
        return True
    return False


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_db()
    print(f"✅ 数据库初始化完成: {DEFAULT_DB_PATH}")

#!/usr/bin/env python3
"""
数据库模型定义
使用 SQLAlchemy ORM
"""
from datetime import datetime
from typing import Optional, List, Dict
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship, Session
import logging

logger = logging.getLogger(__name__)

# 数据库文件路径
DATABASE_URL = "sqlite:///ashare_monitor.db"

# 创建引擎
engine = create_engine(
    DATABASE_URL,
    echo=False,  # 设为 True 可以看到 SQL 语句
    connect_args={"check_same_thread": False}  # SQLite 多线程支持
)

# 创建 Session 工厂
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

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
    
    def __repr__(self):
        return f"<Watchlist(user={self.user_id}, stock={self.stock_code}, name={self.name})>"


# ── 数据库初始化 ──────────────────────────────────────────────────


def init_db():
    """初始化数据库（创建所有表）"""
    Base.metadata.create_all(bind=engine)
    logger.info("数据库初始化完成")


def get_db() -> Session:
    """获取数据库会话"""
    db = SessionLocal()
    try:
        return db
    except Exception as e:
        db.close()
        raise e


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
    # 测试数据库
    logging.basicConfig(level=logging.INFO)
    
    # 初始化数据库
    init_db()
    
    # 测试 CRUD
    db = get_db()
    
    # 创建用户
    user = get_or_create_user(db, 123456789, "测试用户")
    
    # 添加持仓
    add_portfolio(db, 123456789, "600519", "贵州茅台", 1500, 100, -5, 10)
    
    # 添加关注
    add_watchlist(db, 123456789, "300750", "宁德时代")
    
    # 查询
    portfolios = db.query(Portfolio).filter(Portfolio.user_id == 123456789).all()
    print(f"持仓: {portfolios}")
    
    watchlist = db.query(Watchlist).filter(Watchlist.user_id == 123456789).all()
    print(f"关注: {watchlist}")
    
    db.close()
    print("✅ 数据库测试完成")

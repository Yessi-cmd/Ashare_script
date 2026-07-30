#!/usr/bin/env python3
"""
用户配置管理模块 - 数据库版本
使用 SQLAlchemy ORM 替代 YAML 文件
"""
import logging
from typing import Dict, List
from database import init_db, get_db, User, Portfolio, Watchlist

logger = logging.getLogger(__name__)

# 初始化数据库（如果表不存在则创建）
init_db()


def load_user_config(user_id: int, create_user: bool = True) -> dict:
    """
    加载指定用户的配置
    兼容旧的 YAML 格式，返回字典
    """
    db = get_db()
    try:
        user = db.query(User).filter(User.user_id == user_id).first()
        if user is None and create_user:
            user = User(user_id=user_id)
            db.add(user)
            db.commit()
            db.refresh(user)
        
        # 查询持仓
        portfolios = db.query(Portfolio).filter(Portfolio.user_id == user_id).all()
        portfolio_dict = {}
        for p in portfolios:
            portfolio_dict[p.stock_code] = {
                "name": p.name,
                "buy_price": p.buy_price,
                "shares": p.shares,
                "stop_loss": p.stop_loss,
                "take_profit": p.take_profit
            }
        
        # 查询关注池
        watchlist = db.query(Watchlist).filter(Watchlist.user_id == user_id).all()
        watchlist_dict = {w.stock_code: w.name for w in watchlist}
        
        return {
            "user_id": user_id,
            "portfolio": portfolio_dict,
            "watchlist": watchlist_dict
        }
        
    finally:
        db.close()


def save_user_config(user_id: int, config: dict):
    """
    保存用户配置
    从字典格式同步到数据库
    """
    db = get_db()
    try:
        user = db.query(User).filter(User.user_id == user_id).first()
        if user is None:
            db.add(User(user_id=user_id))
            db.flush()

        portfolio_rows = {
            row.stock_code: row
            for row in db.query(Portfolio).filter(Portfolio.user_id == user_id).all()
        }
        requested_portfolio = config.get("portfolio", {})
        for code, row in list(portfolio_rows.items()):
            if code not in requested_portfolio:
                db.delete(row)
        for code, info in requested_portfolio.items():
            row = portfolio_rows.get(code)
            if row is None:
                row = Portfolio(user_id=user_id, stock_code=code)
                db.add(row)
            row.name = info.get("name", code)
            row.buy_price = float(info.get("buy_price", 0))
            row.shares = int(info.get("shares", 0))
            row.stop_loss = float(info.get("stop_loss", -5.0))
            row.take_profit = float(info.get("take_profit", 10.0))

        watchlist_rows = {
            row.stock_code: row
            for row in db.query(Watchlist).filter(Watchlist.user_id == user_id).all()
        }
        requested_watchlist = config.get("watchlist", {})
        for code, row in list(watchlist_rows.items()):
            if code not in requested_watchlist:
                db.delete(row)
        for code, name in requested_watchlist.items():
            row = watchlist_rows.get(code)
            if row is None:
                row = Watchlist(user_id=user_id, stock_code=code)
                db.add(row)
            row.name = name

        db.commit()
        logger.info(f"用户 {user_id} 配置已保存到数据库")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_all_users() -> List[int]:
    """
    获取所有用户ID列表
    """
    db = get_db()
    try:
        users = db.query(User.user_id).all()
        return [user_id for (user_id,) in users]
    finally:
        db.close()


def get_all_portfolios() -> Dict[int, dict]:
    """
    获取所有用户的持仓
    返回: {user_id: {code: {...}}, ...}
    """
    db = get_db()
    try:
        portfolios = db.query(Portfolio).all()
        result = {}
        
        for p in portfolios:
            if p.user_id not in result:
                result[p.user_id] = {}
            
            result[p.user_id][p.stock_code] = {
                "name": p.name,
                "buy_price": p.buy_price,
                "shares": p.shares,
                "stop_loss": p.stop_loss,
                "take_profit": p.take_profit
            }
        
        return result
    finally:
        db.close()


def get_all_watchlists() -> Dict[int, dict]:
    """
    获取所有用户的关注池
    返回: {user_id: {code: name}, ...}
    """
    db = get_db()
    try:
        watchlist = db.query(Watchlist).all()
        result = {}
        
        for w in watchlist:
            if w.user_id not in result:
                result[w.user_id] = {}
            
            result[w.user_id][w.stock_code] = w.name
        
        return result
    finally:
        db.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(f"当前数据库用户数: {len(get_all_users())}")

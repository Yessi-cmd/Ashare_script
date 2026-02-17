#!/usr/bin/env python3
"""
用户配置管理模块 - 数据库版本
使用 SQLAlchemy ORM 替代 YAML 文件
"""
import logging
from typing import Dict, List
from database import (
    init_db, get_db, get_or_create_user,
    add_portfolio as db_add_portfolio,
    remove_portfolio as db_remove_portfolio,
    add_watchlist as db_add_watchlist,
    remove_watchlist as db_remove_watchlist,
    User, Portfolio, Watchlist
)

logger = logging.getLogger(__name__)

# 初始化数据库（如果表不存在则创建）
init_db()


def load_user_config(user_id: int) -> dict:
    """
    加载指定用户的配置
    兼容旧的 YAML 格式，返回字典
    """
    db = get_db()
    try:
        # 确保用户存在
        user = get_or_create_user(db, user_id)
        
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
        # 确保用户存在
        get_or_create_user(db, user_id)
        
        # 获取当前数据库中的持仓和关注
        current_portfolios = {p.stock_code for p in db.query(Portfolio).filter(Portfolio.user_id == user_id).all()}
        current_watchlist = {w.stock_code for w in db.query(Watchlist).filter(Watchlist.user_id == user_id).all()}
        
        # 同步持仓
        new_portfolios = set(config.get("portfolio", {}).keys())
        
        # 删除不在配置中的持仓
        for code in current_portfolios - new_portfolios:
            db_remove_portfolio(db, user_id, code)
        
        # 添加或更新持仓
        for code, info in config.get("portfolio", {}).items():
            db_add_portfolio(
                db, user_id, code,
                name=info.get("name", code),
                buy_price=info.get("buy_price", 0),
                shares=info.get("shares", 0),
                stop_loss=info.get("stop_loss", -5.0),
                take_profit=info.get("take_profit", 10.0)
            )
        
        # 同步关注池
        new_watchlist = set(config.get("watchlist", {}).keys())
        
        # 删除不在配置中的关注
        for code in current_watchlist - new_watchlist:
            db_remove_watchlist(db, user_id, code)
        
        # 添加或更新关注
        for code, name in config.get("watchlist", {}).items():
            db_add_watchlist(db, user_id, code, name)
        
        logger.info(f"用户 {user_id} 配置已保存到数据库")
        
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
    # 测试
    logging.basicConfig(level=logging.INFO)
    
    # 测试用户配置
    test_user = 999888777
    
    # 加载配置（会自动创建用户）
    config = load_user_config(test_user)
    print(f"初始配置: {config}")
    
    # 修改配置
    config["portfolio"]["600519"] = {
        "name": "贵州茅台",
        "buy_price": 1500,
        "shares": 100,
        "stop_loss": -5,
        "take_profit": 10
    }
    config["watchlist"]["300750"] = "宁德时代"
    
    # 保存
    save_user_config(test_user, config)
    
    # 重新加载验证
    config2 = load_user_config(test_user)
    print(f"保存后配置: {config2}")
    
    # 测试获取所有用户
    print(f"所有用户: {get_all_users()}")
    
    # 测试获取所有持仓
    print(f"所有持仓: {get_all_portfolios()}")
    
    print("✅ user_config 数据库版本测试完成")

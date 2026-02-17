"""
A股行情监控 V2 - 策略模块
核心功能：
  1. 综合评分系统（0-100分）→ 买入/观望/远离
  2. 持仓止盈止损检测
  3. 全市场扫描（两阶段筛选）
"""

import logging
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime, timedelta

import akshare as ak
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


# ── 历史数据缓存 ──────────────────────────────────────────────
# 避免重复获取同一只股票的历史数据，减少API调用和流量消耗

_history_cache: dict[str, tuple[pd.DataFrame, datetime]] = {}
CACHE_DURATION = timedelta(minutes=5)  # 缓存5分钟


# ── 告警数据结构 ──────────────────────────────────────────────

@dataclass
class Alert:
    """一条告警信息"""
    stock_code: str
    stock_name: str
    alert_type: str     # buy_signal / sell_signal / stop_loss / take_profit
    level: str          # INFO / WARNING / CRITICAL
    score: int = 0      # 综合评分 0-100
    message: str = ""
    price: float = 0.0
    change_pct: float = 0.0
    extra: dict = field(default_factory=dict)


# ── 实时行情获取 ──────────────────────────────────────────────

def fetch_realtime_quotes(stock_codes: list[str]) -> Optional[pd.DataFrame]:
    """获取实时行情"""
    try:
        df = ak.stock_zh_a_spot_em()
        if df is None or df.empty:
            logger.warning("获取实时行情数据为空")
            return None

        df["代码"] = df["代码"].astype(str).str.zfill(6)
        target = df[df["代码"].isin(stock_codes)].copy()

        if target.empty:
            logger.warning(f"未找到目标股票: {stock_codes}")
            return None

        logger.info(f"成功获取 {len(target)} 只股票行情")
        return target

    except Exception as e:
        logger.error(f"获取实时行情失败: {e}")
        return None


def prefilter_full_market(prefilter_config: dict) -> Optional[pd.DataFrame]:
    """
    全市场预筛选（阶段1）：只用实时报价数据，不获取历史K线
    
    筛选条件：
    - 涨跌幅绝对值 ≥ min_price_change
    - 成交量 > 0
    - 流通市值 ≥ min_market_cap 亿
    
    返回符合条件的股票列表（最多 max_results 只）
    """
    try:
        # 获取全市场实时报价（~5000只股票，约100KB）
        logger.info("开始全市场扫描...")
        df = ak.stock_zh_a_spot_em()
        if df is None or df.empty:
            logger.warning("获取全市场数据为空")
            return None
        
        df["代码"] = df["代码"].astype(str).str.zfill(6)
        total_count = len(df)
        logger.info(f"获取全市场{total_count}只股票报价")
        
        # 筛选条件
        min_price_change = prefilter_config.get("min_price_change", 5.0)
        min_market_cap = prefilter_config.get("min_market_cap", 50)  # 亿
        max_results = prefilter_config.get("max_results", 100)
        
        # 过滤条件
        df_filtered = df[
            (df["涨跌幅"].abs() >= min_price_change) &  # 涨跌幅绝对值
            (df["流通市值"] >= min_market_cap * 1e8) &  # 流通市值（元转亿）
            (df["成交量"] > 0)                          # 有成交量
        ].copy()
        
        if df_filtered.empty:
            logger.info("预筛选：无符合条件的股票")
            return None
        
        # 按涨跌幅排序，取绝对值最大的
        df_filtered["涨跌幅_abs"] = df_filtered["涨跌幅"].abs()
        df_filtered = df_filtered.sort_values("涨跌幅_abs", ascending=False)
        df_filtered = df_filtered.head(max_results)
        
        logger.info(
            f"预筛选完成：{total_count} → {len(df_filtered)} 只股票\n"
            f"  条件：涨跌≥±{min_price_change}%，市值≥{min_market_cap}亿"
        )
        
        return df_filtered
        
    except Exception as e:
        logger.error(f"全市场预筛选失败: {e}")
        return None


def fetch_history(stock_code: str, days: int = 60) -> Optional[pd.DataFrame]:
    """获取历史K线（带缓存，5分钟内不重复获取）"""
    now = datetime.now()
    
    # 检查缓存
    if stock_code in _history_cache:
        cached_data, cached_time = _history_cache[stock_code]
        if now - cached_time < CACHE_DURATION:
            logger.debug(f"使用缓存: {stock_code}")
            return cached_data
    
    # 获取新数据
    try:
        df = ak.stock_zh_a_hist(
            symbol=stock_code,
            period="daily",
            adjust="qfq"
        )
        if df is None or df.empty:
            return None
        
        result = df.tail(days)
        # 更新缓存
        _history_cache[stock_code] = (result, now)
        logger.debug(f"获取并缓存: {stock_code}")
        return result
    except Exception as e:
        logger.error(f"获取 {stock_code} 历史数据失败: {e}")
        return None


# ── 技术指标计算（内部使用，不暴露给用户）────────────────────

def _calc_rsi(closes: pd.Series, period: int = 14) -> float:
    """计算 RSI 指标"""
    delta = closes.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    val = rsi.iloc[-1]
    return float(val) if not pd.isna(val) else 50.0


def _calc_macd(closes: pd.Series) -> dict:
    """计算 MACD 指标"""
    ema12 = closes.ewm(span=12, adjust=False).mean()
    ema26 = closes.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    macd = 2 * (dif - dea)

    return {
        "dif": float(dif.iloc[-1]) if not pd.isna(dif.iloc[-1]) else 0,
        "dea": float(dea.iloc[-1]) if not pd.isna(dea.iloc[-1]) else 0,
        "macd": float(macd.iloc[-1]) if not pd.isna(macd.iloc[-1]) else 0,
        # 金叉：DIF从下方穿越DEA
        "golden_cross": (
            len(dif) >= 2 and
            float(dif.iloc[-2]) < float(dea.iloc[-2]) and
            float(dif.iloc[-1]) > float(dea.iloc[-1])
        ),
        # 死叉：DIF从上方穿越DEA
        "death_cross": (
            len(dif) >= 2 and
            float(dif.iloc[-2]) > float(dea.iloc[-2]) and
            float(dif.iloc[-1]) < float(dea.iloc[-1])
        ),
    }


def _calc_ma_trend(closes: pd.Series, price: float) -> dict:
    """计算均线趋势"""
    ma5 = closes.rolling(5).mean().iloc[-1] if len(closes) >= 5 else price
    ma10 = closes.rolling(10).mean().iloc[-1] if len(closes) >= 10 else price
    ma20 = closes.rolling(20).mean().iloc[-1] if len(closes) >= 20 else price

    return {
        "above_ma5": price > ma5,
        "above_ma10": price > ma10,
        "above_ma20": price > ma20,
        "ma5_rising": len(closes) >= 6 and closes.rolling(5).mean().iloc[-1] > closes.rolling(5).mean().iloc[-2],
    }


def _calc_volume_trend(volumes: pd.Series) -> dict:
    """计算成交量趋势"""
    if len(volumes) < 6:
        return {"surge": False, "ratio": 1.0}

    recent_avg = volumes.iloc[-5:].mean()
    prev_avg = volumes.iloc[-10:-5].mean() if len(volumes) >= 10 else recent_avg

    ratio = recent_avg / prev_avg if prev_avg > 0 else 1.0
    return {
        "surge": ratio > 1.5,
        "ratio": round(float(ratio), 2),
    }


# ── 综合评分系统 ──────────────────────────────────────────────

def calculate_score(stock_code: str, current_price: float,
                    change_pct: float) -> tuple[int, str]:
    """
    综合评分：根据多个技术指标计算 0-100 分。
    ≥ 70 = 买入信号
    40-69 = 观望
    < 40 = 远离/卖出

    返回 (分数, 白话文解释)
    """
    hist = fetch_history(stock_code, days=60)
    if hist is None or len(hist) < 20:
        return 50, "数据不足，暂时无法评估"

    closes = hist["收盘"].astype(float)
    volumes = hist["成交量"].astype(float)

    score = 50  # 基础分
    reasons = []

    # ─── 1. RSI 指标 (权重 25分) ───
    rsi = _calc_rsi(closes)
    if rsi < 30:
        # 超卖区 → 可能反弹
        score += 20
        reasons.append("处于超卖区域，有反弹可能")
    elif rsi < 40:
        score += 10
        reasons.append("价格偏低，有一定吸引力")
    elif rsi > 70:
        # 超买区 → 可能回调
        score -= 20
        reasons.append("短期涨幅过大，有回调风险")
    elif rsi > 60:
        score -= 5
        reasons.append("价格偏高")

    # ─── 2. MACD 指标 (权重 20分) ───
    macd = _calc_macd(closes)
    if macd["golden_cross"]:
        score += 20
        reasons.append("近期出现向上趋势转折")
    elif macd["death_cross"]:
        score -= 20
        reasons.append("近期出现向下趋势转折")
    elif macd["macd"] > 0 and macd["dif"] > macd["dea"]:
        score += 8
        reasons.append("整体趋势向上")
    elif macd["macd"] < 0 and macd["dif"] < macd["dea"]:
        score -= 8
        reasons.append("整体趋势偏弱")

    # ─── 3. 均线位置 (权重 15分) ───
    ma = _calc_ma_trend(closes, current_price)
    ma_above_count = sum([ma["above_ma5"], ma["above_ma10"], ma["above_ma20"]])

    if ma_above_count == 3:
        score += 12
        reasons.append("站上所有均线，形态健康")
    elif ma_above_count == 2:
        score += 5
    elif ma_above_count == 0:
        score -= 10
        reasons.append("跌破所有均线，形态偏弱")

    if ma["ma5_rising"]:
        score += 3

    # ─── 4. 成交量 (权重 10分) ───
    vol = _calc_volume_trend(volumes)
    if vol["surge"] and change_pct > 0:
        score += 10
        reasons.append("成交活跃，资金关注度高")
    elif vol["surge"] and change_pct < 0:
        score -= 10
        reasons.append("放量下跌，资金可能在撤离")

    # ─── 5. 当日涨跌 (权重 5分) ───
    if change_pct > 3:
        score -= 5  # 当天涨太多，追高风险
        reasons.append("今日涨幅较大，追高需谨慎")
    elif change_pct < -5:
        # 大跌可能是机会也可能是风险，给中性
        reasons.append("今日跌幅较大，需关注原因")

    # 限制分数范围
    score = max(0, min(100, score))

    # 生成白话文总结
    if not reasons:
        reasons.append("各项指标中性，没有明显信号")

    summary = "；".join(reasons[:3])  # 最多取3条理由
    return score, summary


# ── 持仓止盈止损检测 ──────────────────────────────────────────

def check_portfolio(row: pd.Series, portfolio_item: dict) -> list[Alert]:
    """检测持仓的止盈止损"""
    alerts = []

    code = str(row["代码"]).zfill(6)
    name = portfolio_item.get("name", code)
    buy_price = float(portfolio_item.get("buy_price", 0))
    shares = int(portfolio_item.get("shares", 0))
    stop_loss_pct = float(portfolio_item.get("stop_loss", -5.0))
    take_profit_pct = float(portfolio_item.get("take_profit", 10.0))

    current_price = float(row.get("最新价", 0))
    if buy_price <= 0 or current_price <= 0:
        return alerts

    # 计算盈亏
    profit_pct = (current_price - buy_price) / buy_price * 100
    profit_amount = (current_price - buy_price) * shares

    # 止损检测
    if profit_pct <= stop_loss_pct:
        alerts.append(Alert(
            stock_code=code,
            stock_name=name,
            alert_type="stop_loss",
            level="CRITICAL",
            score=0,
            price=current_price,
            change_pct=profit_pct,
            message=(
                f"🚨 止损警告 | {name}({code})\n"
                f"现价 ¥{current_price:.2f} | 买入价 ¥{buy_price:.2f}\n"
                f"亏损 {profit_pct:.1f}%（{profit_amount:+,.0f}元）\n"
                f"⚡ 建议立即卖出止损！"
            ),
            extra={
                "buy_price": buy_price,
                "shares": shares,
                "profit_amount": round(profit_amount, 2),
            },
        ))

    # 止盈检测
    elif profit_pct >= take_profit_pct:
        alerts.append(Alert(
            stock_code=code,
            stock_name=name,
            alert_type="take_profit",
            level="WARNING",
            score=0,
            price=current_price,
            change_pct=profit_pct,
            message=(
                f"💰 止盈提醒 | {name}({code})\n"
                f"现价 ¥{current_price:.2f} | 买入价 ¥{buy_price:.2f}\n"
                f"盈利 +{profit_pct:.1f}%（+{profit_amount:,.0f}元）\n"
                f"🎯 已达止盈目标，建议考虑卖出落袋为安！"
            ),
            extra={
                "buy_price": buy_price,
                "shares": shares,
                "profit_amount": round(profit_amount, 2),
            },
        ))

    return alerts


# ── 生成买入/卖出信号 ─────────────────────────────────────────

def generate_signal(row: pd.Series, stock_name: str,
                    signal_config: dict) -> list[Alert]:
    """为关注池股票生成买入/卖出信号"""
    alerts = []

    code = str(row["代码"]).zfill(6)
    price = float(row.get("最新价", 0))
    change_pct = float(row.get("涨跌幅", 0))
    buy_threshold = signal_config.get("buy_threshold", 70)
    sell_threshold = signal_config.get("sell_threshold", 30)

    score, reason = calculate_score(code, price, change_pct)

    if score >= buy_threshold:
        alerts.append(Alert(
            stock_code=code,
            stock_name=stock_name,
            alert_type="buy_signal",
            level="INFO",
            score=score,
            price=price,
            change_pct=change_pct,
            message=(
                f"🟢 买入信号 | {stock_name}({code})\n"
                f"现价 ¥{price:.2f} | 评分 {score}/100\n"
                f"理由：{reason}"
            ),
        ))
    elif score <= sell_threshold:
        alerts.append(Alert(
            stock_code=code,
            stock_name=stock_name,
            alert_type="sell_signal",
            level="WARNING",
            score=score,
            price=price,
            change_pct=change_pct,
            message=(
                f"🔴 远离信号 | {stock_name}({code})\n"
                f"现价 ¥{price:.2f} | 评分 {score}/100\n"
                f"理由：{reason}"
            ),
        ))

    return alerts, score, reason


# ── 主策略调度 ────────────────────────────────────────────────

def run_all_checks(quotes_df: pd.DataFrame, config: dict) -> tuple[list[Alert], dict]:
    """
    执行所有检测，返回 (告警列表, 评分详情)
    支持全市场模式和关注池模式
    """
    all_alerts = []
    score_details = {}  # {代码: (分数, 理由)}

    portfolio = config.get("portfolio", {})
    watchlist = config.get("watchlist", {})
    signal_config = config.get("signal", {})
    full_market = config.get("full_market", {})
    
    # 全市场模式：quotes_df 已经是预筛选后的结果
    if quotes_df is not None and full_market.get("enabled", False):
        scoring_config = full_market.get("scoring", {})
        min_score = scoring_config.get("min_score", 70)
        
        for _, row in quotes_df.iterrows():
            code = str(row["代码"]).zfill(6)
            name = row.get("名称", code)
            price = float(row.get("最新价", 0))
            change_pct = float(row.get("涨跌幅", 0))
            
            # 计算评分（阶段2：详细评分）
            score, reason = calculate_score(code, price, change_pct)
            score_details[code] = (score, reason)
            
            # 只推送高分股票
            if score >= min_score:
                all_alerts.append(Alert(
                    stock_code=code,
                    stock_name=name,
                    alert_type="buy_signal",
                    level="INFO",
                    score=score,
                    price=price,
                    change_pct=change_pct,
                    message=(
                        f"🟢 买入信号 | {name}({code})\n"
                        f"现价 ¥{price:.2f} | 涨跌 {change_pct:+.2f}% | 评分 {score}/100\n"
                        f"理由：{reason}"
                    ),
                ))
    
    # 常规模式：持仓 + 关注池
    else:
        for _, row in quotes_df.iterrows():
            code = str(row["代码"]).zfill(6)

            # 1. 持仓止盈止损检测
            if code in portfolio:
                all_alerts.extend(check_portfolio(row, portfolio[code]))
                # 持仓也计算评分（用于展示）
                price = float(row.get("最新价", 0))
                change_pct = float(row.get("涨跌幅", 0))
                score, reason = calculate_score(code, price, change_pct)
                score_details[code] = (score, reason)

            # 2. 关注池买入/卖出信号
            if code in watchlist:
                name = watchlist[code]
                signals, score, reason = generate_signal(row, name, signal_config)
                all_alerts.extend(signals)
                score_details[code] = (score, reason)

    return all_alerts, score_details

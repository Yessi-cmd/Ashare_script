#!/usr/bin/env python3
"""
A股行情监控 V2 - 主程序
功能：
  - 监控持仓止盈止损
  - 关注池买入信号评分
  - 自动跳过节假日和非交易时段
  - 推送通知到 Telegram / 钉钉 / 邮件

用法:
    python monitor.py              # 持续监控
    python monitor.py --test       # 测试模式（获取一次数据，不发通知）
    python monitor.py --once       # 单次运行（检测一次并发送通知后退出）
"""

import argparse
import logging
import os
import signal
import sys
import time
from datetime import datetime

import yaml

from holidays import is_trading_day, is_holiday, is_weekend, get_holiday_name, get_next_trading_day
from strategies import fetch_realtime_quotes, run_all_checks, prefilter_full_market
from notifier import format_alerts, send_notification
from user_config import get_all_portfolios, get_all_watchlists, get_all_users

# ── 全局 ──────────────────────────────────────────────────────

RUNNING = True


def signal_handler(signum, frame):
    global RUNNING
    RUNNING = False
    logger.info("收到退出信号，正在停止...")


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


# ── 日志 ──────────────────────────────────────────────────────

def setup_logging(config: dict):
    log_cfg = config.get("logging", {})
    level = getattr(logging, log_cfg.get("level", "INFO").upper(), logging.INFO)
    log_file = log_cfg.get("file", "monitor.log")

    fmt = "%(asctime)s [%(levelname)s] %(name)s - %(message)s"
    handlers = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_file, encoding="utf-8"),
    ]
    logging.basicConfig(level=level, format=fmt, handlers=handlers)
    return logging.getLogger(__name__)


# ── 交易时段判断 ──────────────────────────────────────────────

def is_trading_time(config: dict) -> bool:
    """判断当前是否在交易时段（含节假日检测）"""
    now = datetime.now()
    today = now.date()

    # 检查是否为交易日
    if not is_trading_day(today):
        return False

    # 检查是否在交易时间
    trading = config.get("monitor", {}).get("trading_hours", {})
    current_time = now.strftime("%H:%M")

    morning_start = trading.get("morning_start", "09:15")
    morning_end = trading.get("morning_end", "11:30")
    afternoon_start = trading.get("afternoon_start", "13:00")
    afternoon_end = trading.get("afternoon_end", "15:00")

    return (morning_start <= current_time <= morning_end or
            afternoon_start <= current_time <= afternoon_end)


def get_market_status_message() -> str:
    """获取当前市场状态的友好提示"""
    now = datetime.now()
    today = now.date()

    if is_holiday(today):
        name = get_holiday_name(today)
        next_td = get_next_trading_day(today)
        return f"🏮 今天{name}休市 | 下一个交易日: {next_td.strftime('%m月%d日')}"
    elif is_weekend(today):
        next_td = get_next_trading_day(today)
        return f"📅 周末休市 | 下一个交易日: {next_td.strftime('%m月%d日')}"
    else:
        hour = now.hour
        if hour < 9:
            return "⏳ 等待开盘（9:30）..."
        elif 11 < hour < 13:
            return "☕ 午间休市中（13:00开盘）..."
        elif hour >= 15:
            return "🔔 今日已收盘"
        return ""


# ── 行情展示 ──────────────────────────────────────────────────

def print_dashboard(quotes_df, config: dict, score_details: dict):
    """打印行情仪表盘"""
    portfolio = config.get("portfolio", {})
    watchlist = config.get("watchlist", {})

    print("\n" + "=" * 72)
    print(f"  📊 A股行情监控  |  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 72)

    # ─── 持仓部分 ───
    portfolio_rows = quotes_df[quotes_df["代码"].isin(portfolio.keys())]
    if not portfolio_rows.empty:
        print("\n  💼 我的持仓")
        print(f"  {'股票':<12} {'现价':>8} {'今日涨跌':>10} {'持仓盈亏':>12} {'评分':>6}")
        print("  " + "-" * 56)

        total_profit = 0
        for _, row in portfolio_rows.iterrows():
            code = str(row["代码"]).zfill(6)
            p = portfolio[code]
            name = p.get("name", code)
            price = float(row.get("最新价", 0))
            change = float(row.get("涨跌幅", 0))
            buy_price = float(p.get("buy_price", 0))
            shares = int(p.get("shares", 0))

            profit_pct = (price - buy_price) / buy_price * 100 if buy_price > 0 else 0
            profit_amt = (price - buy_price) * shares

            total_profit += profit_amt

            # 盈亏颜色
            if profit_pct > 0:
                pnl_str = f"🔴+{profit_pct:.1f}%"
            elif profit_pct < 0:
                pnl_str = f"🟢{profit_pct:.1f}%"
            else:
                pnl_str = "⚪ 0%"

            # 评分
            score, _ = score_details.get(code, (0, ""))
            score_str = f"{score}分"

            day_change = f"{'🔴' if change > 0 else '🟢'}{change:+.2f}%"

            print(f"  {name:<10} {price:>8.2f} {day_change:>10} "
                  f"{pnl_str:>10} {score_str:>6}")

        print(f"  {'':>30} 持仓总盈亏: {'🔴' if total_profit > 0 else '🟢'}"
              f"{total_profit:+,.0f}元")

    # ─── 关注池 ───
    watch_rows = quotes_df[quotes_df["代码"].isin(watchlist.keys())]
    if not watch_rows.empty:
        print(f"\n  👀 关注池")
        print(f"  {'股票':<12} {'现价':>8} {'今日涨跌':>10} {'评分':>6} {'建议':>8}")
        print("  " + "-" * 56)

        for _, row in watch_rows.iterrows():
            code = str(row["代码"]).zfill(6)
            name = watchlist.get(code, code)
            price = float(row.get("最新价", 0))
            change = float(row.get("涨跌幅", 0))

            score, _ = score_details.get(code, (50, ""))

            if score >= 70:
                advice = "🟢 买入"
            elif score <= 30:
                advice = "🔴 远离"
            else:
                advice = "🟡 观望"

            day_change = f"{'🔴' if change > 0 else '🟢'}{change:+.2f}%"

            print(f"  {name:<10} {price:>8.2f} {day_change:>10} "
                  f"{score:>4}分 {advice:>8}")

    print("\n" + "=" * 72)


# ── 主循环 ────────────────────────────────────────────────────

def monitor_loop(config: dict, test_mode: bool = False, once: bool = False):
    """主监控循环"""
    # 从数据库读取所有用户的持仓和关注池
    portfolios_by_user = get_all_portfolios()  # {user_id: {code: {...}}}
    watchlists_by_user = get_all_watchlists()  # {user_id: {code: name}}
    
    # 合并所有用户的持仓和关注池
    portfolio = {}
    for user_id, user_portfolio in portfolios_by_user.items():
        portfolio.update(user_portfolio)
    
    watchlist = {}
    for user_id, user_watchlist in watchlists_by_user.items():
        watchlist.update(user_watchlist)
    
    total_users = len(get_all_users())
    logger.info(f"📊 监控 {total_users} 个用户的股票池")
    
    full_market = config.get("full_market", {})
    full_market_enabled = full_market.get("enabled", False)
    
    # 全市场模式 vs 关注池模式
    if full_market_enabled:
        logger.info("🌐 全市场扫描模式已启用")
        prefilter_cfg = full_market.get("prefilter", {})
        logger.info(f"  阶段1筛选: 涨跌≥±{prefilter_cfg.get('min_price_change', 5)}%, "
                   f"市值≥{prefilter_cfg.get('min_market_cap', 50)}亿")
        logger.info(f"  阶段2评分: 最多保留{prefilter_cfg.get('max_results', 100)}只股票")
        scoring_cfg = full_market.get("scoring", {})
        logger.info(f"  买入信号: 评分≥{scoring_cfg.get('min_score', 70)}")
        all_codes = []  # 全市场模式不需要预定义股票池
    else:
        all_codes = list(set(list(portfolio.keys()) + list(watchlist.keys())))
        if not all_codes:
            logger.error("股票池为空！请在 config.yaml 中配置 portfolio / watch list 或启用 full_market")
            sys.exit(1)
        
        # 显示监控信息
        logger.info(f"持仓股票: {len(portfolio)} 只")
        for code, info in portfolio.items():
            logger.info(f"  {info.get('name', code)}({code}) "
                       f"买入价 ¥{info.get('buy_price', 0):.2f} × {info.get('shares', 0)}股 "
                       f"止损 {info.get('stop_loss', -5)}% / 止盈 {info.get('take_profit', 10)}%")
        
        logger.info(f"关注股票: {len(watchlist)} 只")
    
    interval = config.get("monitor", {}).get("interval_seconds", 30)
    logger.info(f"轮询间隔: {interval} 秒")

    # 告警去重
    alerted_cache: dict[str, float] = {}
    ALERT_COOLDOWN = 300

    while RUNNING:
        try:
            # 检查交易时段
            if not test_mode and not once and not is_trading_time(config):
                status_msg = get_market_status_message()
                logger.info(f"⏸  {status_msg}")
                time.sleep(60)
                continue

            # 获取行情
            if full_market_enabled:
                # 全市场模式：先预筛选
                logger.info("正在执行全市场预筛选...")
                quotes = prefilter_full_market(full_market.get("prefilter", {}))
                if quotes is None or quotes.empty:
                    logger.info("预筛选无结果，等待下一轮...")
                    if test_mode or once:
                        logger.info("✅ 测试完成（无符合条件的股票）")
                        break
                    time.sleep(interval)
                    continue
            else:
                # 关注池模式
                logger.info("正在获取实时行情...")
                quotes = fetch_realtime_quotes(all_codes)

            if quotes is None or quotes.empty:
                logger.warning("获取行情失败，等待重试...")
                time.sleep(interval)
                continue

            # 执行检测
            alerts, score_details = run_all_checks(quotes, config)

            # 打印仪表盘
            print_dashboard(quotes, config, score_details)

            # 处理告警
            if alerts:
                now_ts = time.time()
                new_alerts = []
                for alert in alerts:
                    key = f"{alert.stock_code}:{alert.alert_type}"
                    last_time = alerted_cache.get(key, 0)
                    if now_ts - last_time >= ALERT_COOLDOWN:
                        new_alerts.append(alert)
                        alerted_cache[key] = now_ts

                if new_alerts:
                    logger.info(f"🔔 发现 {len(new_alerts)} 条提醒！")
                    for a in new_alerts:
                        for line in a.message.split("\n"):
                            logger.info(f"  {line}")

                    if not test_mode:
                        message = format_alerts(new_alerts)
                        # 止损止盈用紧急标题
                        has_urgent = any(a.alert_type in ("stop_loss", "take_profit")
                                         for a in new_alerts)
                        subject = "🚨 A股紧急提醒" if has_urgent else "📊 A股行情提醒"
                        send_notification(message, config, subject=subject)
                    else:
                        print("\n" + format_alerts(new_alerts))
                        print("（测试模式 - 未发送通知）")
            else:
                logger.info("✅ 一切正常，无需提醒")

            if test_mode or once:
                break

            logger.info(f"等待 {interval} 秒...")
            time.sleep(interval)

        except KeyboardInterrupt:
            break
        except Exception as e:
            logger.error(f"监控异常: {e}", exc_info=True)
            time.sleep(interval)

    logger.info("监控已停止")


# ── 入口 ──────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="A股行情监控 V2")
    parser.add_argument("--config", "-c", default="config.yaml",
                        help="配置文件路径 (默认: config.yaml)")
    parser.add_argument("--test", "-t", action="store_true",
                        help="测试模式: 获取一次数据并输出，不发送通知")
    parser.add_argument("--once", "-o", action="store_true",
                        help="单次运行: 检测一次并发送通知后退出")
    args = parser.parse_args()

    config_path = args.config
    if not os.path.exists(config_path):
        print(f"❌ 配置文件不存在: {config_path}")
        sys.exit(1)

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    global logger
    logger = setup_logging(config)

    print()
    print("╔══════════════════════════════════════╗")
    print("║      📊 A股行情监控 V2 启动          ║")
    print("╚══════════════════════════════════════╝")
    print()

    # 检查市场状态
    status_msg = get_market_status_message()
    if status_msg:
        logger.info(status_msg)

    if args.test:
        logger.info("🧪 运行模式: 测试（不发送通知）")
    elif args.once:
        logger.info("🔂 运行模式: 单次")
    else:
        logger.info("🔄 运行模式: 持续监控")

    monitor_loop(config, test_mode=args.test, once=args.once)


if __name__ == "__main__":
    main()

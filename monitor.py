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
import copy
import logging
import signal
import sys
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from holidays import (
    get_holiday_name,
    get_next_trading_day,
    is_calendar_supported,
    is_holiday,
    is_trading_day,
    is_weekend,
)
from alert_store import load_alert_cache, mark_alerted
from strategies import fetch_realtime_quotes, run_all_checks, prefilter_full_market
from snapshot_store import save_quote_snapshots
from notifier import format_alerts, send_notification
from paper_trading import (
    load_paper_monitoring_universe,
    paper_owner_user_id,
    process_pending_paper_orders,
)
from settings import ConfigError, get_owner_user_id, load_config
from user_config import load_user_config

# ── 全局 ──────────────────────────────────────────────────────

RUNNING = True
STOP_EVENT = threading.Event()
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
logger = logging.getLogger(__name__)


def signal_handler(signum, frame):
    global RUNNING
    RUNNING = False
    STOP_EVENT.set()
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
    now = datetime.now(SHANGHAI_TZ)
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
    now = datetime.now(SHANGHAI_TZ)
    today = now.date()

    if is_holiday(today):
        name = get_holiday_name(today)
        try:
            next_td = get_next_trading_day(today)
        except ValueError as exc:
            return f"🏮 今天{name}休市 | {exc}"
        return f"🏮 今天{name}休市 | 下一个交易日: {next_td.strftime('%m月%d日')}"
    elif is_weekend(today):
        try:
            next_td = get_next_trading_day(today)
        except ValueError as exc:
            return f"📅 周末休市 | {exc}"
        return f"📅 周末休市 | 下一个交易日: {next_td.strftime('%m月%d日')}"
    else:
        hour = now.hour
        if hour < 9:
            return "⏳ 等待开盘（9:15）..."
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
    now = datetime.now(SHANGHAI_TZ)
    print(f"  📊 A股行情监控  |  {now.strftime('%Y-%m-%d %H:%M:%S')}")
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
        print("\n  👀 关注池")
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

def build_runtime_config(config: dict) -> tuple[dict, str]:
    """Build one monitoring snapshot from the configured single-user source."""
    runtime_config = copy.deepcopy(config)
    owner_user_id = get_owner_user_id(config)
    if owner_user_id is None:
        runtime_config["portfolio"] = config.get("portfolio", {}) or {}
        runtime_config["watchlist"] = config.get("watchlist", {}) or {}
        return runtime_config, "YAML 本地配置"

    user_config = load_user_config(owner_user_id, create_user=False)
    runtime_config["portfolio"] = user_config.get("portfolio", {})
    runtime_config["watchlist"] = user_config.get("watchlist", {})
    return runtime_config, "SQLite 单用户数据"


def monitor_loop(config: dict, test_mode: bool = False, once: bool = False):
    """主监控循环"""
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

    interval = config.get("monitor", {}).get("interval_seconds", 30)
    logger.info(f"轮询间隔: {interval} 秒")

    # 告警去重
    alert_owner_user_id = get_owner_user_id(config) or 0
    try:
        alerted_cache = load_alert_cache(alert_owner_user_id)
    except Exception as exc:
        logger.warning(f"读取持久化告警状态失败，将使用内存去重: {exc}")
        alerted_cache = {}
    ALERT_COOLDOWN = 300
    previous_codes = None

    while RUNNING:
        try:
            # 检查交易时段
            if not test_mode and not once and not is_trading_time(config):
                status_msg = get_market_status_message()
                logger.info(f"⏸  {status_msg}")
                STOP_EVENT.wait(60)
                continue

            runtime_config, data_source = build_runtime_config(config)
            portfolio = runtime_config.get("portfolio", {})
            watchlist = runtime_config.get("watchlist", {})
            paper_owner = paper_owner_user_id(config)
            try:
                paper_universe = load_paper_monitoring_universe(paper_owner)
            except Exception as exc:
                logger.warning(f"读取模拟盘股票池失败，本轮跳过模拟盘: {exc}")
                paper_universe = {}
            paper_codes = set(paper_universe)
            runtime_config["_paper_codes"] = paper_codes
            all_codes = sorted(set(portfolio) | set(watchlist) | paper_codes)

            current_codes = tuple(all_codes)
            if current_codes != previous_codes and not full_market_enabled:
                logger.info(
                    f"📊 数据源: {data_source} | 持仓 {len(portfolio)} 只 | "
                    f"关注 {len(watchlist)} 只 | 模拟盘 {len(paper_codes)} 只"
                )
                previous_codes = current_codes

            if not full_market_enabled and not all_codes:
                logger.warning("股票池为空，请配置 app.owner_user_id 后通过 Bot 添加，或填写 YAML 股票池")
                if test_mode or once:
                    return False
                STOP_EVENT.wait(interval)
                continue

            # 获取行情
            if full_market_enabled:
                # 全市场模式：先预筛选
                logger.info("正在执行全市场预筛选...")
                quotes = prefilter_full_market(full_market.get("prefilter", {}))
                scan_candidate_codes = set()
                if quotes is not None and not quotes.empty:
                    scan_candidate_codes = {
                        str(code).zfill(6) for code in quotes["代码"].tolist()
                    }
                runtime_config["_full_market_candidate_codes"] = scan_candidate_codes
                if paper_codes:
                    paper_quotes = fetch_realtime_quotes(sorted(paper_codes))
                    if paper_quotes is not None and not paper_quotes.empty:
                        if quotes is None or quotes.empty:
                            quotes = paper_quotes
                        else:
                            quotes = pd.concat(
                                [quotes, paper_quotes], ignore_index=True
                            ).drop_duplicates(subset=["代码"], keep="last")
                if quotes is None or quotes.empty:
                    logger.info("预筛选无结果，等待下一轮...")
                    if test_mode or once:
                        logger.info("✅ 测试完成（无符合条件的股票）")
                        break
                    STOP_EVENT.wait(interval)
                    continue
            else:
                # 关注池模式
                logger.info("正在获取实时行情...")
                quotes = fetch_realtime_quotes(all_codes)

            if quotes is None or quotes.empty:
                logger.warning("获取行情失败，等待重试...")
                if test_mode or once:
                    return False
                STOP_EVENT.wait(interval)
                continue

            # 执行检测
            alerts, score_details = run_all_checks(quotes, runtime_config)

            try:
                save_quote_snapshots(quotes, score_details)
            except Exception as exc:
                logger.warning(f"保存 Web 行情快照失败，不影响本轮告警: {exc}")

            if paper_codes and not test_mode:
                try:
                    process_pending_paper_orders(
                        quotes,
                        paper_owner,
                        config,
                    )
                except Exception as exc:
                    logger.warning(f"模拟盘撮合失败，本轮订单保持待成交: {exc}")

            # 打印仪表盘
            print_dashboard(quotes, runtime_config, score_details)

            # 处理告警
            if alerts:
                now_ts = time.time()
                new_alerts = []
                for alert in alerts:
                    key = f"{alert.stock_code}:{alert.alert_type}"
                    last_time = alerted_cache.get(key, 0)
                    if now_ts - last_time >= ALERT_COOLDOWN:
                        new_alerts.append(alert)

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
                        results = send_notification(message, config, subject=subject)
                        delivered = not results or any(results.values())
                        if delivered:
                            for alert in new_alerts:
                                key = f"{alert.stock_code}:{alert.alert_type}"
                                alerted_cache[key] = now_ts
                            try:
                                mark_alerted(alert_owner_user_id, new_alerts, now_ts)
                            except Exception as exc:
                                logger.warning(f"保存告警去重状态失败: {exc}")
                        else:
                            logger.warning("所有通知渠道均发送失败，将在下一轮重试")
                    else:
                        print("\n" + format_alerts(new_alerts))
                        print("（测试模式 - 未发送通知）")
                        for alert in new_alerts:
                            key = f"{alert.stock_code}:{alert.alert_type}"
                            alerted_cache[key] = now_ts
            else:
                logger.info("✅ 一切正常，无需提醒")

            if test_mode or once:
                return True

            logger.info(f"等待 {interval} 秒...")
            STOP_EVENT.wait(interval)

        except KeyboardInterrupt:
            break
        except Exception as e:
            logger.error(f"监控异常: {e}", exc_info=True)
            if test_mode or once:
                return False
            STOP_EVENT.wait(interval)

    logger.info("监控已停止")
    return True


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
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        print(f"❌ {exc}")
        sys.exit(2)

    current_year = datetime.now(SHANGHAI_TZ).year
    if not is_calendar_supported(current_year):
        print(f"❌ 尚未配置 {current_year} 年 A 股交易日历，请先更新 holidays.py")
        sys.exit(2)

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

    success = monitor_loop(config, test_mode=args.test, once=args.once)
    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()

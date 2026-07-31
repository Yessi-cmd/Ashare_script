#!/usr/bin/env python3
"""Collect cross-market benchmark quotes for the read-only Web dashboard.

Usage:
    python market_monitor.py              # continuous polling
    python market_monitor.py --once       # fetch once and exit
    python market_monitor.py --test       # fetch once, print, no notifications
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading

from global_market_data import (
    fetch_market_quotes,
    global_markets_enabled,
    market_definitions,
    market_poll_interval,
    market_request_timeout,
    save_market_snapshots,
)
from settings import ConfigError, load_config

RUNNING = True
STOP_EVENT = threading.Event()
logger = logging.getLogger(__name__)

# 全部行情失败时的退避上限（秒），避免长时间限流时反复高频重试。
MAX_BACKOFF_SECONDS = 60 * 60
BACKOFF_FACTOR = 3.0


def _backoff_wait(interval: float, failures: int) -> float:
    """指数退避：连续失败时逐步拉长等待，最多 MAX_BACKOFF_SECONDS。"""
    return min(interval * (BACKOFF_FACTOR ** max(failures, 1)), MAX_BACKOFF_SECONDS)


def signal_handler(signum, frame):
    del signum, frame
    global RUNNING
    RUNNING = False
    STOP_EVENT.set()
    logger.info("收到退出信号，正在停止跨市场监控...")


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


def setup_logging(config: dict):
    log_cfg = config.get("logging", {})
    level = getattr(logging, str(log_cfg.get("level", "INFO")).upper(), logging.INFO)
    log_file = log_cfg.get("file", "monitor.log")
    handlers = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_file, encoding="utf-8"),
    ]
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        handlers=handlers,
    )
    return logging.getLogger(__name__)


def run_market_cycle(config: dict, test_mode: bool = False) -> tuple[int, list[str]]:
    """Fetch and persist one cycle; return saved count and isolated errors."""

    definitions = market_definitions(config, include_a_share=True)
    if not definitions:
        logger.info("跨市场监控已禁用或没有配置指数")
        return 0, []

    quotes, errors = fetch_market_quotes(
        definitions,
        timeout=market_request_timeout(config),
    )
    for error in errors:
        logger.warning(f"跨市场行情获取失败: {error}")

    saved = save_market_snapshots(quotes) if quotes else 0
    if quotes:
        print_market_cycle(quotes, test_mode=test_mode)
        logger.info(f"跨市场行情快照已更新: {saved} 条，失败 {len(errors)} 条")
    else:
        logger.warning("本轮没有可保存的跨市场行情快照，保留已有数据")
    return saved, errors


def print_market_cycle(quotes, test_mode: bool = False):
    """Print a compact terminal view useful for manual test runs."""

    print("\n" + "=" * 72)
    print("  🌏 基准指数行情 | A股 · 港股 · 韩国 · 美股 · 日本")
    print("=" * 72)
    for quote in quotes:
        direction = "🔴" if quote.change_pct > 0 else "🟢" if quote.change_pct < 0 else "⚪"
        print(
            f"  {quote.name:<12} {quote.price:>12,.2f} {direction}"
            f" {quote.change_pct:+.2f}%  {quote.currency}"
        )
    if test_mode:
        print("  （测试模式 - 未发送通知）")
    print("=" * 72)


def market_monitor_loop(
    config: dict,
    test_mode: bool = False,
    once: bool = False,
) -> bool:
    """Run the cross-market collector independently of A-share hours."""

    if not global_markets_enabled(config):
        logger.info("global_markets.enabled=false，跨市场采集器保持待机")
        if test_mode or once:
            return True
        while RUNNING:
            STOP_EVENT.wait(3600)
        return True

    interval = market_poll_interval(config)
    logger.info(f"跨市场轮询间隔: {interval:g} 秒")
    failures = 0
    while RUNNING:
        try:
            saved, errors = run_market_cycle(config, test_mode=test_mode)
            if test_mode or once:
                return bool(saved) or not errors
            if not saved:
                failures += 1
                wait = _backoff_wait(interval, failures)
                logger.warning(
                    f"本轮跨市场行情全部失败（{len(errors)} 条错误），"
                    f"第 {failures} 次连续失败，下次重试等待 {wait:g} 秒"
                )
                STOP_EVENT.wait(wait)
            else:
                failures = 0
                STOP_EVENT.wait(interval)
        except KeyboardInterrupt:
            break
        except Exception as exc:
            logger.error(f"跨市场监控异常: {exc}", exc_info=True)
            if test_mode or once:
                return False
            failures += 1
            STOP_EVENT.wait(_backoff_wait(interval, failures))

    logger.info("跨市场监控已停止")
    return True


def main():
    parser = argparse.ArgumentParser(description="A股与全球基准指数行情采集器")
    parser.add_argument(
        "--config", "-c", default="config.yaml", help="配置文件路径 (默认: config.yaml)"
    )
    parser.add_argument(
        "--test", "-t", action="store_true", help="测试模式: 获取一次并输出"
    )
    parser.add_argument(
        "--once", "-o", action="store_true", help="单次获取并退出"
    )
    args = parser.parse_args()

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"❌ {exc}")
        sys.exit(2)

    global logger
    logger = setup_logging(config)
    logger.info("🌏 A股与全球基准指数行情采集器启动")
    if args.test:
        logger.info("🧪 运行模式: 测试")
    elif args.once:
        logger.info("🔂 运行模式: 单次")
    else:
        logger.info("🔄 运行模式: 持续监控")

    success = market_monitor_loop(config, test_mode=args.test, once=args.once)
    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()

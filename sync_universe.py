#!/usr/bin/env python3
"""Synchronize the personal universe and market proxy for server timers."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, timedelta

from market_data import MarketDataError, sync_daily_bars
from research_universe import RESEARCH_UNIVERSE
from settings import ConfigError, get_owner_user_id, load_config
from user_config import load_user_config


def universe_codes(config: dict) -> list[str]:
    owner_user_id = get_owner_user_id(config)
    if owner_user_id is not None:
        personal = load_user_config(owner_user_id, create_user=False)
        portfolio = personal.get("portfolio", {})
        watchlist = personal.get("watchlist", {})
    else:
        portfolio = config.get("portfolio", {}) or {}
        watchlist = config.get("watchlist", {}) or {}
    return sorted(set(portfolio) | set(watchlist) | set(RESEARCH_UNIVERSE))


def main() -> int:
    parser = argparse.ArgumentParser(description="同步个人研究池与沪深 300 日线")
    parser.add_argument(
        "--config",
        default=os.getenv("ASHARE_CONFIG_PATH", "config.yaml"),
        help="配置文件路径",
    )
    parser.add_argument("--days", type=int, default=400, help="股票刷新自然日数")
    parser.add_argument(
        "--source", choices=("auto", "eastmoney", "sina"), default="auto"
    )
    args = parser.parse_args()
    if args.days < 120:
        parser.error("--days 不能小于 120")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    )
    try:
        config = load_config(args.config)
        codes = universe_codes(config)
    except ConfigError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 2

    end = date.today()
    start = end - timedelta(days=args.days)
    failures = []
    for code in codes:
        try:
            sync_daily_bars(code, start, end, "qfq", source=args.source)
        except (MarketDataError, ValueError) as exc:
            logging.error("%s 同步失败: %s", code, exc)
            failures.append(code)
    try:
        sync_daily_bars("000300", start, end, "raw", source="index")
    except (MarketDataError, ValueError) as exc:
        logging.error("沪深 300 同步失败: %s", exc)
        failures.append("000300")

    if failures:
        print(f"❌ 同步完成但有 {len(failures)} 个失败: {', '.join(failures)}")
        return 1
    print(f"✅ 已同步 {len(codes)} 只个人股票和沪深 300")
    return 0


if __name__ == "__main__":
    sys.exit(main())

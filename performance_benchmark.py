#!/usr/bin/env python3
"""Repeatable local benchmarks for dashboard reads and pure V2 scoring."""

from __future__ import annotations

import argparse
import statistics
import sys
import time

from dashboard_data import load_overview
from market_data import load_daily_bars, normalize_stock_code
from settings import ConfigError, load_config
from strategies import calculate_score_from_history


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(len(ordered) * fraction + 0.999) - 1))
    return ordered[index]


def benchmark_overview(config: dict, iterations: int) -> tuple[float, float]:
    load_overview(config)
    timings = []
    for _ in range(iterations):
        started = time.perf_counter()
        load_overview(config)
        timings.append((time.perf_counter() - started) * 1000)
    return statistics.median(timings), percentile(timings, 0.95)


def benchmark_v2_scores(codes: list[str], iterations: int) -> tuple[float, float]:
    histories = {}
    for code in codes:
        bars = load_daily_bars(code, adjust="qfq").tail(60).copy()
        if len(bars) < 20:
            continue
        bars["收盘"] = bars["close"]
        bars["成交量"] = bars["volume"]
        histories[code] = bars
    if not histories:
        raise ValueError("指定代码没有足够的本地 qfq 日线")

    timings = []
    for _ in range(iterations):
        started = time.perf_counter()
        for bars in histories.values():
            close = float(bars.iloc[-1]["close"])
            previous = float(bars.iloc[-2]["close"])
            calculate_score_from_history(
                bars,
                close,
                (close / previous - 1) * 100,
            )
        timings.append((time.perf_counter() - started) * 1000)
    return statistics.median(timings), percentile(timings, 0.95)


def main() -> int:
    parser = argparse.ArgumentParser(description="本地可重复性能基准")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--iterations", type=int, default=50)
    parser.add_argument("--codes", nargs="*", default=[])
    args = parser.parse_args()
    if args.iterations < 5:
        parser.error("--iterations 不能小于 5")
    try:
        config = load_config(args.config)
        overview_p50, overview_p95 = benchmark_overview(config, args.iterations)
        print(
            f"总览本地查询: P50 {overview_p50:.2f}ms | "
            f"P95 {overview_p95:.2f}ms | {args.iterations} 次"
        )
        if args.codes:
            codes = [normalize_stock_code(code) for code in args.codes]
            score_p50, score_p95 = benchmark_v2_scores(codes, args.iterations)
            print(
                f"V2 本地评分 {len(codes)} 只: P50 {score_p50:.2f}ms | "
                f"P95 {score_p95:.2f}ms | {args.iterations} 轮"
            )
    except (ConfigError, OSError, ValueError) as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

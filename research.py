#!/usr/bin/env python3
"""CLI for local daily-bar synchronization and baseline backtests."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from baseline_strategies import score_ma_trend
from backtest_engine import BacktestConfig, BacktestResult, run_backtest
from market_data import (
    MarketDataError,
    load_daily_bars,
    normalize_stock_code,
    sync_daily_bars,
)
from strategies import calculate_score_from_history
from strategy_v3 import make_strategy_v3_scorer, score_strategy_v3
from strategy_comparison import ComparisonResult, compare_strategies
from walk_forward import WalkForwardResult, run_walk_forward
from portfolio_backtest import PortfolioConfig, PortfolioResult, run_portfolio_backtest


def parse_date(value: str) -> date:
    for pattern in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(value, pattern).date()
        except ValueError:
            continue
    raise argparse.ArgumentTypeError("日期格式必须是 YYYY-MM-DD 或 YYYYMMDD")


def parse_horizons(value: str) -> tuple[int, ...]:
    try:
        horizons = tuple(sorted({int(item.strip()) for item in value.split(",")}))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("周期必须是逗号分隔的正整数") from exc
    if not horizons or any(item <= 0 for item in horizons):
        raise argparse.ArgumentTypeError("周期必须是逗号分隔的正整数")
    return horizons


def parse_thresholds(value: str) -> tuple[int, ...]:
    thresholds = parse_horizons(value)
    if any(item > 100 for item in thresholds):
        raise argparse.ArgumentTypeError("阈值必须在 1-100 之间")
    return thresholds


def normalize_adjust_argument(value: str) -> str:
    return "" if value == "raw" else value


def resolve_scorer(
    strategy: str,
    market_code: Optional[str],
    start: Optional[date],
    end: Optional[date],
    market_adjust: str,
):
    if strategy == "v2":
        return calculate_score_from_history
    if strategy == "ma":
        return score_ma_trend
    if not market_code:
        return score_strategy_v3
    code = normalize_stock_code(market_code)
    market_bars = load_daily_bars(code, start, end, market_adjust)
    if market_bars.empty:
        raise ValueError(f"市场环境代码 {code} 没有本地日线，请先执行 sync")
    return make_strategy_v3_scorer(market_bars)


def format_backtest(result: BacktestResult) -> str:
    lines = [
        f"📈 回测结果 | {result.stock_code}",
        f"区间: {result.start_date} 至 {result.end_date} | {result.bar_count} 根日线",
        f"买入信号: {result.signal_count} 次 | 买入持有基准: {result.benchmark_return_pct:+.2f}%",
        "",
        "周期  交易数  胜率     平均收益   复合收益   最大回撤   盈亏比",
    ]
    for horizon in result.config.horizons:
        summary = result.summaries[horizon]
        profit_factor = "∞" if summary.profit_factor is None and summary.trade_count else (
            "-" if summary.profit_factor is None else f"{summary.profit_factor:.2f}"
        )
        lines.append(
            f"{horizon:>2}日  {summary.trade_count:>4}  {summary.win_rate_pct:>6.2f}%  "
            f"{summary.average_return_pct:>+8.2f}%  {summary.compounded_return_pct:>+8.2f}%  "
            f"{summary.max_drawdown_pct:>+8.2f}%  {profit_factor:>6}"
        )
    lines.extend([
        "",
        "说明: 信号在收盘后生成，下一交易日开盘进入；已计入佣金、印花税和滑点。",
    ])
    return "\n".join(lines)


def format_comparison(result: ComparisonResult) -> str:
    lines = [
        f"🧪 策略对照 | 持有 {result.horizon} 个交易日",
        "",
        "股票      策略  交易数   胜率     平均收益   复合收益   最大回撤",
    ]
    for code, strategies in result.stock_results.items():
        for name, backtest in strategies.items():
            summary = backtest.summaries[result.horizon]
            lines.append(
                f"{code}  {name:>4}  {summary.trade_count:>4}  "
                f"{summary.win_rate_pct:>6.2f}%  {summary.average_return_pct:>+8.2f}%  "
                f"{summary.compounded_return_pct:>+8.2f}%  {summary.max_drawdown_pct:>+8.2f}%"
            )
    lines.extend(["", "汇总（所有股票信号池）"])
    for name, aggregate in result.aggregates.items():
        summary = aggregate.summary
        lines.append(
            f"{name}: {summary.trade_count} 笔 | 胜率 {summary.win_rate_pct:.2f}% | "
            f"平均 {summary.average_return_pct:+.2f}% | 盈亏比 "
            f"{summary.profit_factor if summary.profit_factor is not None else '∞'}"
        )
    return "\n".join(lines)


def format_walk_forward(result: WalkForwardResult) -> str:
    lines = [
        f"🧭 样本外评估 | {result.strategy_name} | 持有 {result.horizon} 个交易日",
        f"训练: {result.train_start} 至 {result.train_end}",
        f"验证: {result.validation_start} 至 {result.validation_end}",
        "",
        "阈值  训练交易数  平均收益   最大回撤   盈亏比   选择分",
    ]
    for candidate in result.candidates:
        summary = candidate.training_summary
        profit_factor = "∞" if summary.profit_factor is None and summary.trade_count else (
            "-" if summary.profit_factor is None else f"{summary.profit_factor:.2f}"
        )
        selection_score = (
            f"{candidate.selection_score:+.3f}"
            if candidate.selection_score is not None else "样本不足"
        )
        marker = " *" if candidate.threshold == result.selected_threshold else ""
        lines.append(
            f"{candidate.threshold:>3}{marker:<2}  {summary.trade_count:>8}  "
            f"{summary.average_return_pct:>+8.2f}%  "
            f"{summary.max_drawdown_pct:>+8.2f}%  {profit_factor:>6}  "
            f"{selection_score:>8}"
        )
    training = result.training.aggregates[result.strategy_name].summary
    validation = result.validation.aggregates[result.strategy_name].summary
    lines.extend([
        "",
        f"仅由训练集选出的阈值: {result.selected_threshold}",
        f"训练集: {training.trade_count} 笔 | 胜率 {training.win_rate_pct:.2f}% | "
        f"平均 {training.average_return_pct:+.2f}% | 复合 {training.compounded_return_pct:+.2f}%",
        f"验证集: {validation.trade_count} 笔 | 胜率 {validation.win_rate_pct:.2f}% | "
        f"平均 {validation.average_return_pct:+.2f}% | 复合 {validation.compounded_return_pct:+.2f}%",
        "说明: 验证期数据未参与阈值选择，验证前日线只用于指标预热。",
    ])
    return "\n".join(lines)


def format_portfolio(result: PortfolioResult) -> str:
    summary = result.summary
    lines = [
        f"📊 组合回测 | {result.strategy_name}",
        f"区间: {summary.start_date} 至 {summary.end_date} | {summary.trading_days} 个交易日",
        f"调仓: {summary.rebalance_count} 次 | Top-{result.config.top_n} | "
        f"每 {result.config.rebalance_every} 个交易日",
        "",
        "收益       年化收益   年化波动   夏普    最大回撤   换手率",
        f"{summary.total_return_pct:+8.2f}%  {summary.annualized_return_pct:+8.2f}%  "
        f"{summary.annualized_volatility_pct:>8.2f}%  "
        f"{summary.sharpe if summary.sharpe is not None else '-':>6}  "
        f"{summary.max_drawdown_pct:+8.2f}%  {summary.turnover_pct:>7.2f}%",
    ]
    if summary.benchmark_return_pct is not None:
        lines.append(
            f"基准收益: {summary.benchmark_return_pct:+.2f}% | "
            f"超额收益: {summary.excess_return_pct:+.2f}%"
        )
    lines.extend([
        "",
        "说明: 同一交易日只按横截面可见数据排名，下一交易日开盘调仓；已计入成本。",
    ])
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="A股本地行情与策略研究工具")
    subparsers = parser.add_subparsers(dest="command", required=True)

    sync_parser = subparsers.add_parser("sync", help="同步一只股票的本地日线")
    sync_parser.add_argument("stock_code", help="股票代码")
    sync_parser.add_argument("--start", type=parse_date, help="开始日期")
    sync_parser.add_argument("--end", type=parse_date, help="结束日期")
    sync_parser.add_argument("--adjust", choices=("qfq", "hfq", "raw"), default="qfq")
    sync_parser.add_argument(
        "--source", choices=("auto", "eastmoney", "sina", "index"), default="auto"
    )

    backtest_parser = subparsers.add_parser("backtest", help="运行评分策略基线回测")
    backtest_parser.add_argument("stock_code", help="股票代码")
    backtest_parser.add_argument("--start", type=parse_date, help="开始日期")
    backtest_parser.add_argument("--end", type=parse_date, help="结束日期")
    backtest_parser.add_argument("--adjust", choices=("qfq", "hfq", "raw"), default="qfq")
    backtest_parser.add_argument(
        "--source", choices=("auto", "eastmoney", "sina"), default="auto"
    )
    backtest_parser.add_argument("--sync", action="store_true", help="回测前同步请求区间")
    backtest_parser.add_argument(
        "--strategy", choices=("v2", "v3", "ma"), default="v2"
    )
    backtest_parser.add_argument("--market-code", help="V3 市场环境代理代码")
    backtest_parser.add_argument(
        "--market-adjust", choices=("qfq", "hfq", "raw"), default="raw"
    )
    backtest_parser.add_argument("--threshold", type=int, default=70, help="买入评分阈值")
    backtest_parser.add_argument("--horizons", type=parse_horizons, default=(1, 5, 10, 20))
    backtest_parser.add_argument("--commission", type=float, default=0.0003)
    backtest_parser.add_argument("--stamp-duty", type=float, default=0.0005)
    backtest_parser.add_argument("--slippage", type=float, default=0.0005)
    backtest_parser.add_argument("--json", action="store_true", help="输出 JSON")
    backtest_parser.add_argument("--output", type=Path, help="把完整 JSON 写入文件")

    compare_parser = subparsers.add_parser("compare", help="跨股票比较 V2 和 V3")
    compare_parser.add_argument("stock_codes", nargs="+", help="一个或多个股票代码")
    compare_parser.add_argument("--start", type=parse_date, help="开始日期")
    compare_parser.add_argument("--end", type=parse_date, help="结束日期")
    compare_parser.add_argument("--adjust", choices=("qfq", "hfq", "raw"), default="qfq")
    compare_parser.add_argument(
        "--source", choices=("auto", "eastmoney", "sina"), default="auto"
    )
    compare_parser.add_argument("--sync", action="store_true", help="比较前同步请求区间")
    compare_parser.add_argument("--horizon", type=int, default=20)
    compare_parser.add_argument("--threshold", type=int, default=70)
    compare_parser.add_argument("--market-code", help="V3 市场环境代理代码")
    compare_parser.add_argument(
        "--market-adjust", choices=("qfq", "hfq", "raw"), default="raw"
    )
    compare_parser.add_argument("--json", action="store_true", help="输出 JSON")
    compare_parser.add_argument("--output", type=Path, help="把完整 JSON 写入文件")

    walk_parser = subparsers.add_parser(
        "walk-forward", help="在训练集选参数并在后续验证集评估"
    )
    walk_parser.add_argument("stock_codes", nargs="+", help="一个或多个股票代码")
    walk_parser.add_argument("--train-start", type=parse_date, required=True)
    walk_parser.add_argument("--train-end", type=parse_date, required=True)
    walk_parser.add_argument("--validation-start", type=parse_date, required=True)
    walk_parser.add_argument("--validation-end", type=parse_date, required=True)
    walk_parser.add_argument(
        "--strategy", choices=("v2", "v3", "ma"), default="v2"
    )
    walk_parser.add_argument("--market-code", help="V3 市场环境代理代码")
    walk_parser.add_argument(
        "--market-adjust", choices=("qfq", "hfq", "raw"), default="raw"
    )
    walk_parser.add_argument(
        "--thresholds", type=parse_thresholds, default=(60, 65, 70, 75, 80)
    )
    walk_parser.add_argument("--horizon", type=int, default=20)
    walk_parser.add_argument("--minimum-training-trades", type=int, default=10)
    walk_parser.add_argument("--adjust", choices=("qfq", "hfq", "raw"), default="qfq")
    walk_parser.add_argument("--commission", type=float, default=0.0003)
    walk_parser.add_argument("--stamp-duty", type=float, default=0.0005)
    walk_parser.add_argument("--slippage", type=float, default=0.0005)
    walk_parser.add_argument("--json", action="store_true", help="输出 JSON")
    walk_parser.add_argument("--output", type=Path, help="把完整 JSON 写入文件")

    portfolio_parser = subparsers.add_parser(
        "portfolio-backtest", help="运行横截面选股组合回测"
    )
    portfolio_parser.add_argument("stock_codes", nargs="+", help="研究池股票代码")
    portfolio_parser.add_argument("--start", type=parse_date, help="评估开始日期")
    portfolio_parser.add_argument("--end", type=parse_date, help="评估结束日期")
    portfolio_parser.add_argument(
        "--adjust", choices=("qfq", "hfq", "raw"), default="qfq"
    )
    portfolio_parser.add_argument("--top-n", type=int, default=3)
    portfolio_parser.add_argument("--rebalance-every", type=int, default=5)
    portfolio_parser.add_argument("--lookback-bars", type=int, default=126)
    portfolio_parser.add_argument(
        "--market-filter",
        choices=("none", "close_above_ma20", "trend"),
        default="none",
        help="独立市场现金暴露开关",
    )
    portfolio_parser.add_argument("--benchmark-code", default="000300")
    portfolio_parser.add_argument(
        "--benchmark-adjust", choices=("qfq", "hfq", "raw"), default="raw"
    )
    portfolio_parser.add_argument("--commission", type=float, default=0.0003)
    portfolio_parser.add_argument("--stamp-duty", type=float, default=0.0005)
    portfolio_parser.add_argument("--slippage", type=float, default=0.0005)
    portfolio_parser.add_argument("--json", action="store_true", help="输出 JSON")
    portfolio_parser.add_argument("--output", type=Path, help="把完整 JSON 写入文件")
    return parser


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    )
    args = build_parser().parse_args()
    try:
        adjust = normalize_adjust_argument(args.adjust)
        if args.command == "sync":
            code = normalize_stock_code(args.stock_code)
            count = sync_daily_bars(code, args.start, args.end, adjust, source=args.source)
            print(f"✅ {code} 同步完成，本次写入或更新 {count} 条日线")
            return 0

        if args.command == "compare":
            codes = [normalize_stock_code(code) for code in args.stock_codes]
            bars_by_stock = {}
            for code in codes:
                if args.sync:
                    sync_daily_bars(code, args.start, args.end, adjust, source=args.source)
                bars_by_stock[code] = load_daily_bars(code, args.start, args.end, adjust)
            v3_scorer = resolve_scorer(
                "v3",
                args.market_code,
                args.start,
                args.end,
                normalize_adjust_argument(args.market_adjust),
            )
            comparison = compare_strategies(
                bars_by_stock,
                {"v2": calculate_score_from_history, "v3": v3_scorer},
                horizon=args.horizon,
                buy_threshold=args.threshold,
            )
            payload = comparison.to_dict()
            if args.output:
                args.output.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                print(f"完整结果已写入: {args.output}")
            print(
                json.dumps(payload, ensure_ascii=False, indent=2)
                if args.json else format_comparison(comparison)
            )
            return 0

        if args.command == "walk-forward":
            codes = [normalize_stock_code(code) for code in args.stock_codes]
            bars_by_stock = {
                code: load_daily_bars(
                    code, args.train_start, args.validation_end, adjust
                )
                for code in codes
            }
            scorer = resolve_scorer(
                args.strategy,
                args.market_code,
                args.train_start,
                args.validation_end,
                normalize_adjust_argument(args.market_adjust),
            )
            result = run_walk_forward(
                bars_by_stock=bars_by_stock,
                scorer=scorer,
                strategy_name=args.strategy,
                train_start=args.train_start,
                train_end=args.train_end,
                validation_start=args.validation_start,
                validation_end=args.validation_end,
                thresholds=args.thresholds,
                horizon=args.horizon,
                minimum_training_trades=args.minimum_training_trades,
                commission_rate=args.commission,
                stamp_duty_rate=args.stamp_duty,
                slippage_rate=args.slippage,
            )
            payload = result.to_dict()
            if args.output:
                args.output.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                print(f"完整结果已写入: {args.output}")
            print(
                json.dumps(payload, ensure_ascii=False, indent=2)
                if args.json else format_walk_forward(result)
            )
            return 0

        if args.command == "portfolio-backtest":
            codes = [normalize_stock_code(code) for code in args.stock_codes]
            bars_by_stock = {
                code: load_daily_bars(code, None, args.end, normalize_adjust_argument(args.adjust))
                for code in codes
            }
            benchmark = load_daily_bars(
                normalize_stock_code(args.benchmark_code),
                None,
                args.end,
                normalize_adjust_argument(args.benchmark_adjust),
            )
            config = PortfolioConfig(
                stock_codes=tuple(codes),
                lookback_bars=args.lookback_bars,
                top_n=args.top_n,
                rebalance_every=args.rebalance_every,
                market_filter=args.market_filter,
                commission_rate=args.commission,
                stamp_duty_rate=args.stamp_duty,
                slippage_rate=args.slippage,
            )
            result = run_portfolio_backtest(
                bars_by_stock,
                config,
                benchmark_bars=benchmark,
                evaluation_start=args.start,
                evaluation_end=args.end,
            )
            payload = result.to_dict()
            if args.output:
                args.output.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                print(f"完整结果已写入: {args.output}")
            print(
                json.dumps(payload, ensure_ascii=False, indent=2)
                if args.json else format_portfolio(result)
            )
            return 0

        code = normalize_stock_code(args.stock_code)
        if args.sync:
            sync_daily_bars(code, args.start, args.end, adjust, source=args.source)
        bars = load_daily_bars(code, args.start, args.end, adjust)
        config = BacktestConfig(
            stock_code=code,
            strategy_name=args.strategy,
            horizons=args.horizons,
            buy_threshold=args.threshold,
            commission_rate=args.commission,
            stamp_duty_rate=args.stamp_duty,
            slippage_rate=args.slippage,
        )
        scorer = resolve_scorer(
            args.strategy,
            args.market_code,
            args.start,
            args.end,
            normalize_adjust_argument(args.market_adjust),
        )
        result = run_backtest(bars, config, scorer=scorer)
        payload = result.to_dict()
        if args.output:
            args.output.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"完整结果已写入: {args.output}")
        print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else format_backtest(result))
        return 0
    except (MarketDataError, ValueError, OSError) as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

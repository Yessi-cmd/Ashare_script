"""Cross-stock comparison utilities for candidate strategies."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import Optional

import pandas as pd

from backtest_engine import (
    BacktestConfig,
    BacktestResult,
    HorizonSummary,
    ScoreLookup,
    ScoreFunction,
    _summarize,
    run_backtest,
)


@dataclass(frozen=True)
class StrategyAggregate:
    strategy_name: str
    stock_count: int
    benchmark_average_pct: float
    summary: HorizonSummary


@dataclass(frozen=True)
class ComparisonResult:
    horizon: int
    stock_results: dict[str, dict[str, BacktestResult]]
    aggregates: dict[str, StrategyAggregate]

    def to_dict(self) -> dict:
        return {
            "horizon": self.horizon,
            "stock_results": {
                code: {name: result.to_dict() for name, result in strategies.items()}
                for code, strategies in self.stock_results.items()
            },
            "aggregates": {
                name: {
                    "strategy_name": aggregate.strategy_name,
                    "stock_count": aggregate.stock_count,
                    "benchmark_average_pct": aggregate.benchmark_average_pct,
                    "summary": asdict(aggregate.summary),
                }
                for name, aggregate in self.aggregates.items()
            },
        }


def compare_strategies(
    bars_by_stock: dict[str, pd.DataFrame],
    strategy_scorers: dict[str, ScoreFunction],
    horizon: int = 20,
    buy_threshold: int = 70,
    commission_rate: float = 0.0003,
    stamp_duty_rate: float = 0.0005,
    slippage_rate: float = 0.0005,
    warmup_bars: int = 60,
    evaluation_start: Optional[date] = None,
    evaluation_end: Optional[date] = None,
    score_lookups: Optional[dict[str, dict[str, ScoreLookup]]] = None,
) -> ComparisonResult:
    if not bars_by_stock:
        raise ValueError("没有可比较的股票数据")
    if not strategy_scorers:
        raise ValueError("没有可比较的策略")

    stock_results = {}
    for code, bars in bars_by_stock.items():
        stock_results[code] = {}
        for strategy_name, scorer in strategy_scorers.items():
            config = BacktestConfig(
                stock_code=code,
                strategy_name=strategy_name,
                horizons=(horizon,),
                warmup_bars=warmup_bars,
                buy_threshold=buy_threshold,
                commission_rate=commission_rate,
                stamp_duty_rate=stamp_duty_rate,
                slippage_rate=slippage_rate,
            )
            stock_results[code][strategy_name] = run_backtest(
                bars,
                config,
                scorer=scorer,
                evaluation_start=evaluation_start,
                evaluation_end=evaluation_end,
                score_lookup=(score_lookups or {}).get(code, {}).get(strategy_name),
            )

    aggregates = {}
    for strategy_name in strategy_scorers:
        results = [strategies[strategy_name] for strategies in stock_results.values()]
        pooled_trades = sorted(
            [trade for result in results for trade in result.trades[horizon]],
            key=lambda trade: (trade.signal_date, trade.entry_date),
        )
        aggregates[strategy_name] = StrategyAggregate(
            strategy_name=strategy_name,
            stock_count=len(results),
            benchmark_average_pct=round(
                sum(result.benchmark_return_pct for result in results) / len(results), 4
            ),
            summary=_summarize(horizon, pooled_trades),
        )
    return ComparisonResult(horizon, stock_results, aggregates)

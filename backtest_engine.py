"""Deterministic walk-forward backtest baseline for the technical score."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from statistics import mean, median
from typing import Callable, Optional

import pandas as pd

from strategies import calculate_score_from_history


@dataclass(frozen=True)
class BacktestConfig:
    stock_code: str
    strategy_name: str = "v2"
    horizons: tuple[int, ...] = (1, 5, 10, 20)
    warmup_bars: int = 60
    buy_threshold: int = 70
    commission_rate: float = 0.0003
    stamp_duty_rate: float = 0.0005
    slippage_rate: float = 0.0005

    def validate(self) -> None:
        if not self.stock_code:
            raise ValueError("stock_code 不能为空")
        if not self.strategy_name.strip():
            raise ValueError("strategy_name 不能为空")
        if self.warmup_bars < 20:
            raise ValueError("warmup_bars 不能小于 20")
        if not 1 <= self.buy_threshold <= 100:
            raise ValueError("buy_threshold 必须在 1-100 之间")
        if not self.horizons or any(value <= 0 for value in self.horizons):
            raise ValueError("horizons 必须是正整数")
        costs = (self.commission_rate, self.stamp_duty_rate, self.slippage_rate)
        if any(value < 0 or value >= 0.1 for value in costs):
            raise ValueError("交易成本率必须在 0-0.1 之间")


@dataclass(frozen=True)
class BacktestTrade:
    signal_date: date
    entry_date: date
    exit_date: date
    score: int
    entry_price: float
    exit_price: float
    gross_return_pct: float
    net_return_pct: float

    def to_dict(self) -> dict:
        result = asdict(self)
        for key in ("signal_date", "entry_date", "exit_date"):
            result[key] = result[key].isoformat()
        return result


@dataclass(frozen=True)
class HorizonSummary:
    horizon: int
    trade_count: int
    win_rate_pct: float
    average_return_pct: float
    median_return_pct: float
    compounded_return_pct: float
    max_drawdown_pct: float
    profit_factor: Optional[float]


@dataclass(frozen=True)
class BacktestResult:
    stock_code: str
    start_date: date
    end_date: date
    bar_count: int
    signal_count: int
    benchmark_return_pct: float
    config: BacktestConfig
    summaries: dict[int, HorizonSummary]
    trades: dict[int, list[BacktestTrade]]

    def to_dict(self) -> dict:
        return {
            "stock_code": self.stock_code,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "bar_count": self.bar_count,
            "signal_count": self.signal_count,
            "benchmark_return_pct": self.benchmark_return_pct,
            "config": asdict(self.config),
            "summaries": {
                str(horizon): asdict(summary)
                for horizon, summary in self.summaries.items()
            },
            "trades": {
                str(horizon): [trade.to_dict() for trade in trades]
                for horizon, trades in self.trades.items()
            },
        }


ScoreFunction = Callable[[pd.DataFrame, float, float], tuple[int, str]]
ScoreLookup = dict[date, int]


def _validate_bars(bars: pd.DataFrame) -> pd.DataFrame:
    required = {"trade_date", "open", "high", "low", "close", "volume"}
    missing = required - set(bars.columns)
    if missing:
        raise ValueError(f"回测数据缺少字段: {', '.join(sorted(missing))}")
    normalized = bars.copy()
    normalized["trade_date"] = pd.to_datetime(normalized["trade_date"], errors="coerce").dt.date
    for column in ("open", "high", "low", "close", "volume"):
        normalized[column] = pd.to_numeric(normalized[column], errors="coerce")
    normalized = normalized.dropna(subset=list(required))
    normalized = normalized.sort_values("trade_date")
    normalized = normalized.drop_duplicates(subset=["trade_date"], keep="last")
    normalized = normalized.reset_index(drop=True)
    if normalized.empty:
        raise ValueError("回测数据为空")
    if (normalized[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError("回测价格必须大于 0")
    return normalized


def _strategy_history(bars: pd.DataFrame, end_index: int, window: int) -> pd.DataFrame:
    start_index = max(0, end_index - window + 1)
    window_bars = bars.iloc[start_index:end_index + 1]
    columns = ["trade_date", "open", "high", "low", "close", "volume"]
    if "stock_code" in window_bars:
        columns.insert(0, "stock_code")
    history = window_bars[columns].reset_index(drop=True).copy()
    history["收盘"] = history["close"]
    history["成交量"] = history["volume"]
    return history


def _find_buy_signals(
    bars: pd.DataFrame,
    config: BacktestConfig,
    scorer: ScoreFunction,
    evaluation_start: Optional[date] = None,
    evaluation_end: Optional[date] = None,
    score_lookup: Optional[ScoreLookup] = None,
) -> list[tuple[int, int]]:
    signals = []
    previous_score = 50
    for index in range(config.warmup_bars - 1, len(bars) - 1):
        signal_date = bars.iloc[index]["trade_date"]
        score = score_lookup.get(signal_date) if score_lookup is not None else None
        if score is None:
            close = float(bars.iloc[index]["close"])
            previous_close = float(bars.iloc[index - 1]["close"])
            change_pct = (close - previous_close) / previous_close * 100
            history = _strategy_history(bars, index, config.warmup_bars)
            score, _reason = scorer(history, close, change_pct)
        in_evaluation = (
            (evaluation_start is None or signal_date >= evaluation_start)
            and (evaluation_end is None or signal_date <= evaluation_end)
        )
        if (
            in_evaluation
            and score >= config.buy_threshold
            and previous_score < config.buy_threshold
        ):
            signals.append((index, score))
        previous_score = score
    return signals


def calculate_score_lookup(
    bars: pd.DataFrame,
    warmup_bars: int,
    scorer: ScoreFunction,
) -> ScoreLookup:
    """Calculate the deterministic score trace once for threshold sweeps."""
    if warmup_bars < 20:
        raise ValueError("warmup_bars 不能小于 20")
    normalized = _validate_bars(bars)
    if len(normalized) <= warmup_bars:
        raise ValueError(f"评分轨迹至少需要 {warmup_bars + 1} 根日线")
    scores = {}
    for index in range(warmup_bars - 1, len(normalized) - 1):
        close = float(normalized.iloc[index]["close"])
        previous_close = float(normalized.iloc[index - 1]["close"])
        change_pct = (close - previous_close) / previous_close * 100
        history = _strategy_history(normalized, index, warmup_bars)
        score, _reason = scorer(history, close, change_pct)
        scores[normalized.iloc[index]["trade_date"]] = score
    return scores


def _net_return(entry_price: float, exit_price: float,
                config: BacktestConfig) -> tuple[float, float]:
    gross = (exit_price / entry_price - 1) * 100
    effective_entry = entry_price * (1 + config.commission_rate + config.slippage_rate)
    effective_exit = exit_price * (
        1 - config.commission_rate - config.stamp_duty_rate - config.slippage_rate
    )
    net = (effective_exit / effective_entry - 1) * 100
    return gross, net


def _simulate_horizon(bars: pd.DataFrame, signals: list[tuple[int, int]], horizon: int,
                      config: BacktestConfig) -> list[BacktestTrade]:
    trades = []
    next_available_index = 0
    for signal_index, score in signals:
        entry_index = signal_index + 1
        exit_index = entry_index + horizon - 1
        if entry_index < next_available_index or exit_index >= len(bars):
            continue
        entry_price = float(bars.iloc[entry_index]["open"])
        exit_price = float(bars.iloc[exit_index]["close"])
        gross, net = _net_return(entry_price, exit_price, config)
        trades.append(BacktestTrade(
            signal_date=bars.iloc[signal_index]["trade_date"],
            entry_date=bars.iloc[entry_index]["trade_date"],
            exit_date=bars.iloc[exit_index]["trade_date"],
            score=score,
            entry_price=round(entry_price, 4),
            exit_price=round(exit_price, 4),
            gross_return_pct=round(gross, 4),
            net_return_pct=round(net, 4),
        ))
        next_available_index = exit_index + 1
    return trades


def _summarize(horizon: int, trades: list[BacktestTrade]) -> HorizonSummary:
    returns = [trade.net_return_pct for trade in trades]
    if not returns:
        return HorizonSummary(horizon, 0, 0.0, 0.0, 0.0, 0.0, 0.0, None)

    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    for value in returns:
        equity *= 1 + value / 100
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, (equity / peak - 1) * 100)

    gains = sum(value for value in returns if value > 0)
    losses = abs(sum(value for value in returns if value < 0))
    profit_factor = round(gains / losses, 4) if losses > 0 else None
    return HorizonSummary(
        horizon=horizon,
        trade_count=len(returns),
        win_rate_pct=round(sum(value > 0 for value in returns) / len(returns) * 100, 2),
        average_return_pct=round(mean(returns), 4),
        median_return_pct=round(median(returns), 4),
        compounded_return_pct=round((equity - 1) * 100, 4),
        max_drawdown_pct=round(max_drawdown, 4),
        profit_factor=profit_factor,
    )


def run_backtest(
    bars: pd.DataFrame,
    config: BacktestConfig,
    scorer: ScoreFunction = calculate_score_from_history,
    evaluation_start: Optional[date] = None,
    evaluation_end: Optional[date] = None,
    score_lookup: Optional[ScoreLookup] = None,
) -> BacktestResult:
    """Run a no-lookahead, next-open baseline backtest."""
    config.validate()
    bars = _validate_bars(bars)
    if evaluation_start and evaluation_end and evaluation_start > evaluation_end:
        raise ValueError("evaluation_start 不能晚于 evaluation_end")
    if evaluation_end:
        bars = bars[bars["trade_date"] <= evaluation_end].reset_index(drop=True)
    minimum_bars = config.warmup_bars + max(config.horizons)
    if len(bars) < minimum_bars:
        raise ValueError(f"回测至少需要 {minimum_bars} 根日线，当前只有 {len(bars)} 根")

    evaluation_mask = pd.Series(True, index=bars.index)
    if evaluation_start:
        evaluation_mask &= bars["trade_date"] >= evaluation_start
    if evaluation_end:
        evaluation_mask &= bars["trade_date"] <= evaluation_end
    evaluation_indexes = bars.index[evaluation_mask]
    if evaluation_indexes.empty:
        raise ValueError("评估区间内没有日线数据")
    if evaluation_start and evaluation_indexes[0] < config.warmup_bars - 1:
        raise ValueError(f"评估区间前至少需要 {config.warmup_bars - 1} 根预热日线")

    signals = _find_buy_signals(
        bars,
        config,
        scorer,
        evaluation_start=evaluation_start,
        evaluation_end=evaluation_end,
        score_lookup=score_lookup,
    )
    trades = {
        horizon: _simulate_horizon(bars, signals, horizon, config)
        for horizon in config.horizons
    }
    summaries = {
        horizon: _summarize(horizon, horizon_trades)
        for horizon, horizon_trades in trades.items()
    }

    benchmark_index = (
        int(evaluation_indexes[0]) if evaluation_start else config.warmup_bars
    )
    benchmark_entry = float(bars.iloc[benchmark_index]["open"])
    benchmark_exit = float(bars.iloc[int(evaluation_indexes[-1])]["close"])
    _gross, benchmark_net = _net_return(benchmark_entry, benchmark_exit, config)
    return BacktestResult(
        stock_code=config.stock_code,
        start_date=bars.iloc[int(evaluation_indexes[0])]["trade_date"],
        end_date=bars.iloc[int(evaluation_indexes[-1])]["trade_date"],
        bar_count=len(evaluation_indexes),
        signal_count=len(signals),
        benchmark_return_pct=round(benchmark_net, 4),
        config=config,
        summaries=summaries,
        trades=trades,
    )

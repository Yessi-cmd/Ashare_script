"""Realistic, deterministic portfolio backtest for cross-sectional selection."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from math import sqrt
from typing import Optional

import pandas as pd

from cross_sectional_strategy import (
    CandidateScore,
    SelectionConfig,
    prepare_bars,
    rank_universe_on_date,
)


@dataclass(frozen=True)
class PortfolioConfig:
    stock_codes: tuple[str, ...]
    strategy_name: str = "cross_sectional_v1"
    lookback_bars: int = 126
    top_n: int = 3
    rebalance_every: int = 5
    initial_capital: float = 1.0
    commission_rate: float = 0.0003
    stamp_duty_rate: float = 0.0005
    slippage_rate: float = 0.0005
    min_price: float = 1.0
    min_amount: float = 50_000_000.0
    max_volatility_pct: float = 100.0
    max_drawdown_pct: float = -50.0
    market_filter: str = "none"

    def validate(self) -> None:
        if not self.stock_codes:
            raise ValueError("stock_codes 不能为空")
        if len(set(self.stock_codes)) != len(self.stock_codes):
            raise ValueError("stock_codes 不能重复")
        if not self.strategy_name.strip():
            raise ValueError("strategy_name 不能为空")
        if self.lookback_bars < 66:
            raise ValueError("lookback_bars 不能小于 66")
        if self.top_n < 1 or self.top_n > len(self.stock_codes):
            raise ValueError("top_n 必须在 1 和股票数量之间")
        if self.rebalance_every < 1:
            raise ValueError("rebalance_every 必须大于 0")
        if self.initial_capital <= 0:
            raise ValueError("initial_capital 必须大于 0")
        costs = (
            self.commission_rate,
            self.stamp_duty_rate,
            self.slippage_rate,
        )
        if any(value < 0 or value >= 0.1 for value in costs):
            raise ValueError("交易成本率必须在 0-0.1 之间")
        if self.market_filter not in {"none", "close_above_ma20", "trend"}:
            raise ValueError("market_filter 必须是 none、close_above_ma20 或 trend")


@dataclass(frozen=True)
class Rebalance:
    signal_date: date
    entry_date: date
    selected: tuple[str, ...]
    scores: dict[str, float]
    turnover_pct: float
    cost_pct: float

    def to_dict(self) -> dict:
        result = asdict(self)
        result["signal_date"] = self.signal_date.isoformat()
        result["entry_date"] = self.entry_date.isoformat()
        return result


@dataclass(frozen=True)
class PortfolioSummary:
    start_date: date
    end_date: date
    trading_days: int
    rebalance_count: int
    total_return_pct: float
    annualized_return_pct: float
    annualized_volatility_pct: float
    sharpe: Optional[float]
    max_drawdown_pct: float
    turnover_pct: float
    benchmark_return_pct: Optional[float]
    excess_return_pct: Optional[float]

    def to_dict(self) -> dict:
        result = asdict(self)
        result["start_date"] = self.start_date.isoformat()
        result["end_date"] = self.end_date.isoformat()
        return result


@dataclass(frozen=True)
class PortfolioResult:
    strategy_name: str
    config: PortfolioConfig
    summary: PortfolioSummary
    equity_curve: list[dict]
    rebalances: list[Rebalance]

    def to_dict(self) -> dict:
        return {
            "strategy_name": self.strategy_name,
            "config": asdict(self.config),
            "summary": self.summary.to_dict(),
            "equity_curve": self.equity_curve,
            "rebalances": [rebalance.to_dict() for rebalance in self.rebalances],
        }


def _aligned_dates(frames: dict[str, pd.DataFrame]) -> list[date]:
    date_sets = [set(frame["trade_date"]) for frame in frames.values()]
    if not date_sets:
        return []
    return sorted(set.intersection(*date_sets))


def _selection_config(config: PortfolioConfig) -> SelectionConfig:
    return SelectionConfig(
        lookback_bars=config.lookback_bars,
        top_n=config.top_n,
        rebalance_every=config.rebalance_every,
        min_price=config.min_price,
        min_amount=config.min_amount,
        max_volatility_pct=config.max_volatility_pct,
        max_drawdown_pct=config.max_drawdown_pct,
    )


def _weights_from_holdings(
    holdings: dict[str, float],
    cash: float,
    open_prices: dict[str, float],
) -> tuple[float, dict[str, float]]:
    values = {
        code: quantity * open_prices[code]
        for code, quantity in holdings.items()
        if code in open_prices
    }
    equity = cash + sum(values.values())
    if equity <= 0:
        return equity, {}
    return equity, {code: value / equity for code, value in values.items()}


def _rebalance(
    holdings: dict[str, float],
    cash: float,
    selected: list[CandidateScore],
    open_prices: dict[str, float],
    config: PortfolioConfig,
) -> tuple[dict[str, float], float, float, float]:
    equity, old_weights = _weights_from_holdings(holdings, cash, open_prices)
    if equity <= 0:
        return {}, 0.0, 0.0, 0.0

    target_codes = [item.stock_code for item in selected if item.stock_code in open_prices]
    target_weight = 1.0 / len(target_codes) if target_codes else 0.0
    target_weights = {code: target_weight for code in target_codes}
    all_codes = set(old_weights) | set(target_weights)
    buy_turnover = sum(
        max(target_weights.get(code, 0.0) - old_weights.get(code, 0.0), 0.0)
        for code in all_codes
    )
    sell_turnover = sum(
        max(old_weights.get(code, 0.0) - target_weights.get(code, 0.0), 0.0)
        for code in all_codes
    )
    turnover = buy_turnover + sell_turnover
    cost = equity * (
        config.commission_rate * turnover
        + config.stamp_duty_rate * sell_turnover
        + config.slippage_rate * turnover
    )
    investable = max(0.0, equity - cost)
    new_holdings = {
        code: investable * weight / open_prices[code]
        for code, weight in target_weights.items()
    }
    new_cash = investable * max(0.0, 1.0 - sum(target_weights.values()))
    return new_holdings, new_cash, turnover, cost / equity if equity else 0.0


def _benchmark_curve(
    benchmark_bars: Optional[pd.DataFrame],
    dates: list[date],
    first_entry: date,
    initial_capital: float,
) -> Optional[dict[date, float]]:
    if benchmark_bars is None or benchmark_bars.empty:
        return None
    benchmark = prepare_bars(benchmark_bars).set_index("trade_date")
    if first_entry not in benchmark.index:
        return None
    available = [day for day in dates if day in benchmark.index]
    if not available or available[0] != first_entry:
        return None
    shares = initial_capital / float(benchmark.loc[first_entry, "open"])
    return {
        day: shares * float(benchmark.loc[day, "close"])
        for day in available
    }


def _market_allows(
    benchmark_bars: Optional[pd.DataFrame],
    signal_date: date,
    market_filter: str,
) -> bool:
    """Evaluate a market exposure gate using bars through ``signal_date`` only."""
    if market_filter == "none":
        return True
    if benchmark_bars is None or benchmark_bars.empty:
        raise ValueError("启用 market_filter 时必须提供 benchmark_bars")
    benchmark = benchmark_bars[benchmark_bars["trade_date"] <= signal_date]
    if len(benchmark) < 60:
        return False
    closes = benchmark["close"]
    close = float(closes.iloc[-1])
    ma20 = float(closes.tail(20).mean())
    ma60 = float(closes.tail(60).mean())
    if market_filter == "close_above_ma20":
        return close > ma20
    return close > ma20 > ma60


def run_portfolio_backtest(
    bars_by_stock: dict[str, pd.DataFrame],
    config: PortfolioConfig,
    benchmark_bars: Optional[pd.DataFrame] = None,
    evaluation_start: Optional[date] = None,
    evaluation_end: Optional[date] = None,
) -> PortfolioResult:
    """Run a date-aligned, next-open portfolio backtest."""
    config.validate()
    normalized = {
        str(code).zfill(6): prepare_bars(frame)
        for code, frame in bars_by_stock.items()
    }
    missing_codes = set(config.stock_codes) - set(normalized)
    if missing_codes:
        raise ValueError(f"缺少股票数据: {', '.join(sorted(missing_codes))}")
    normalized = {code: normalized[code] for code in config.stock_codes}
    dates = _aligned_dates(normalized)
    if evaluation_end is not None:
        dates = [day for day in dates if day <= evaluation_end]
    if evaluation_start is not None:
        evaluation_dates = [day for day in dates if day >= evaluation_start]
    else:
        evaluation_dates = dates
    if len(dates) <= config.lookback_bars or not evaluation_dates:
        raise ValueError("对齐后的日线不足以执行组合回测")
    if evaluation_start and evaluation_dates[0] not in dates:
        raise ValueError("评估区间内没有日线")

    frame_by_code = {
        code: frame.set_index("trade_date") for code, frame in normalized.items()
    }
    prepared_benchmark = (
        None if benchmark_bars is None else prepare_bars(benchmark_bars)
    )
    selection_config = _selection_config(config)

    signal_dates = []
    first_signal_index = config.lookback_bars - 1
    for index in range(first_signal_index, len(dates) - 1, config.rebalance_every):
        signal_date = dates[index]
        entry_date = dates[index + 1]
        if evaluation_start and signal_date < evaluation_start:
            continue
        signal_dates.append((signal_date, entry_date))
    if not signal_dates:
        raise ValueError("评估区间内没有可执行的调仓信号")

    targets: dict[date, tuple[date, list[CandidateScore]]] = {}
    for signal_date, entry_date in signal_dates:
        ranked = rank_universe_on_date(
            normalized,
            signal_date,
            selection_config,
            top_n=config.top_n,
        )
        if not _market_allows(
            prepared_benchmark, signal_date, config.market_filter
        ):
            ranked = []
        targets[entry_date] = (signal_date, ranked)

    first_entry = signal_dates[0][1]
    simulation_dates = [day for day in dates if day >= first_entry]
    holdings: dict[str, float] = {}
    cash = config.initial_capital
    equity_curve = []
    rebalances = []
    total_turnover = 0.0
    previous_equity = config.initial_capital

    for day in simulation_dates:
        open_prices = {
            code: float(frame_by_code[code].loc[day, "open"])
            for code in normalized
        }
        close_prices = {
            code: float(frame_by_code[code].loc[day, "close"])
            for code in normalized
        }
        if day in targets:
            signal_date, selected = targets[day]
            holdings, cash, turnover, cost_pct = _rebalance(
                holdings, cash, selected, open_prices, config
            )
            total_turnover += turnover
            rebalances.append(Rebalance(
                signal_date=signal_date,
                entry_date=day,
                selected=tuple(item.stock_code for item in selected),
                scores={item.stock_code: item.score for item in selected},
                turnover_pct=round(turnover * 100, 6),
                cost_pct=round(cost_pct * 100, 6),
            ))

        equity_open = cash + sum(
            quantity * open_prices[code]
            for code, quantity in holdings.items()
        )
        equity_close = cash + sum(
            quantity * close_prices[code]
            for code, quantity in holdings.items()
        )
        equity_curve.append({
            "trade_date": day.isoformat(),
            "equity": round(equity_close, 10),
            "open_equity": round(equity_open, 10),
            "daily_return_pct": round(
                (equity_close / previous_equity - 1) * 100 if previous_equity else 0.0,
                8,
            ),
            "holdings": sorted(holdings),
        })
        previous_equity = equity_close

    equity = pd.Series(
        [row["equity"] for row in equity_curve],
        index=pd.to_datetime([row["trade_date"] for row in equity_curve]),
        dtype="float64",
    )
    daily_returns = equity.pct_change().dropna()
    peak = equity.cummax()
    drawdown = equity / peak - 1
    trading_days = len(equity)
    total_return = (float(equity.iloc[-1]) / config.initial_capital - 1) * 100
    annualized_return = (
        ((float(equity.iloc[-1]) / config.initial_capital) ** (252 / trading_days) - 1) * 100
        if trading_days > 0 and equity.iloc[-1] > 0 else -100.0
    )
    annualized_vol = float(daily_returns.std(ddof=0) * sqrt(252) * 100) if len(daily_returns) else 0.0
    sharpe = (
        float(daily_returns.mean() / daily_returns.std(ddof=0) * sqrt(252))
        if len(daily_returns) > 1 and daily_returns.std(ddof=0) > 0 else None
    )
    benchmark_curve = _benchmark_curve(
        prepared_benchmark, simulation_dates, first_entry, config.initial_capital
    )
    benchmark_return = None
    excess_return = None
    if benchmark_curve:
        benchmark_return = (benchmark_curve[simulation_dates[-1]] / config.initial_capital - 1) * 100
        excess_return = total_return - benchmark_return

    summary = PortfolioSummary(
        start_date=simulation_dates[0],
        end_date=simulation_dates[-1],
        trading_days=trading_days,
        rebalance_count=len(rebalances),
        total_return_pct=round(total_return, 6),
        annualized_return_pct=round(annualized_return, 6),
        annualized_volatility_pct=round(annualized_vol, 6),
        sharpe=None if sharpe is None else round(sharpe, 6),
        max_drawdown_pct=round(float(drawdown.min() * 100), 6),
        turnover_pct=round(total_turnover * 100, 6),
        benchmark_return_pct=None if benchmark_return is None else round(benchmark_return, 6),
        excess_return_pct=None if excess_return is None else round(excess_return, 6),
    )
    return PortfolioResult(
        strategy_name=config.strategy_name,
        config=config,
        summary=summary,
        equity_curve=equity_curve,
        rebalances=rebalances,
    )

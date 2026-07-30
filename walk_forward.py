"""Out-of-sample parameter selection for stock scoring strategies."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from math import inf
from typing import Optional

import pandas as pd

from backtest_engine import HorizonSummary, ScoreFunction, calculate_score_lookup
from strategy_comparison import ComparisonResult, compare_strategies


@dataclass(frozen=True)
class ThresholdCandidate:
    threshold: int
    eligible: bool
    selection_score: Optional[float]
    training_summary: HorizonSummary


@dataclass(frozen=True)
class WalkForwardResult:
    strategy_name: str
    horizon: int
    train_start: date
    train_end: date
    validation_start: date
    validation_end: date
    minimum_training_trades: int
    selected_threshold: int
    candidates: tuple[ThresholdCandidate, ...]
    training: ComparisonResult
    validation: ComparisonResult

    def to_dict(self) -> dict:
        return {
            "strategy_name": self.strategy_name,
            "horizon": self.horizon,
            "train_start": self.train_start.isoformat(),
            "train_end": self.train_end.isoformat(),
            "validation_start": self.validation_start.isoformat(),
            "validation_end": self.validation_end.isoformat(),
            "minimum_training_trades": self.minimum_training_trades,
            "selected_threshold": self.selected_threshold,
            "candidates": [
                {
                    **asdict(candidate),
                    "training_summary": asdict(candidate.training_summary),
                }
                for candidate in self.candidates
            ],
            "training": self.training.to_dict(),
            "validation": self.validation.to_dict(),
        }


def _slice_dates(
    bars: pd.DataFrame,
    start: Optional[date],
    end: date,
) -> pd.DataFrame:
    dates = pd.to_datetime(bars["trade_date"], errors="coerce").dt.date
    mask = dates <= end
    if start:
        mask &= dates >= start
    return bars.loc[mask].reset_index(drop=True).copy()


def _validation_bars(
    bars: pd.DataFrame,
    validation_start: date,
    validation_end: date,
    warmup_bars: int,
) -> pd.DataFrame:
    dates = pd.to_datetime(bars["trade_date"], errors="coerce").dt.date
    prior = bars.loc[dates < validation_start].tail(warmup_bars)
    validation = bars.loc[(dates >= validation_start) & (dates <= validation_end)]
    return pd.concat([prior, validation], ignore_index=True)


def _selection_score(summary: HorizonSummary) -> float:
    """Balance return quality and drawdown without rewarding infinite PF."""
    profit_factor = min(summary.profit_factor or 3.0, 3.0)
    return round(
        summary.average_return_pct
        + 0.25 * (profit_factor - 1.0)
        + 0.02 * summary.max_drawdown_pct,
        6,
    )


def run_walk_forward(
    bars_by_stock: dict[str, pd.DataFrame],
    scorer: ScoreFunction,
    strategy_name: str,
    train_start: date,
    train_end: date,
    validation_start: date,
    validation_end: date,
    thresholds: tuple[int, ...] = (60, 65, 70, 75, 80),
    horizon: int = 20,
    minimum_training_trades: int = 10,
    warmup_bars: int = 60,
    commission_rate: float = 0.0003,
    stamp_duty_rate: float = 0.0005,
    slippage_rate: float = 0.0005,
) -> WalkForwardResult:
    """Select a threshold on training data, then evaluate it once out of sample."""
    if not bars_by_stock:
        raise ValueError("没有可评估的股票数据")
    if train_start > train_end:
        raise ValueError("训练开始日期不能晚于结束日期")
    if validation_start > validation_end:
        raise ValueError("验证开始日期不能晚于结束日期")
    if train_end >= validation_start:
        raise ValueError("训练区间必须严格早于验证区间且不能重叠")
    thresholds = tuple(sorted(set(thresholds)))
    if not thresholds or any(value < 1 or value > 100 for value in thresholds):
        raise ValueError("候选阈值必须在 1-100 之间")
    if minimum_training_trades < 1:
        raise ValueError("minimum_training_trades 必须大于 0")

    normalized_bars = {}
    for code, bars in bars_by_stock.items():
        normalized = bars.copy()
        normalized["stock_code"] = code
        normalized_bars[code] = normalized
    training_bars = {
        code: _slice_dates(bars, train_start, train_end)
        for code, bars in normalized_bars.items()
    }
    validation_bars = {
        code: _validation_bars(bars, validation_start, validation_end, warmup_bars)
        for code, bars in normalized_bars.items()
    }
    training_score_lookups = {
        code: {strategy_name: calculate_score_lookup(bars, warmup_bars, scorer)}
        for code, bars in training_bars.items()
    }
    validation_score_lookups = {
        code: {strategy_name: calculate_score_lookup(bars, warmup_bars, scorer)}
        for code, bars in validation_bars.items()
    }

    comparisons: dict[int, ComparisonResult] = {}
    candidates = []
    for threshold in thresholds:
        comparison = compare_strategies(
            training_bars,
            {strategy_name: scorer},
            horizon=horizon,
            buy_threshold=threshold,
            commission_rate=commission_rate,
            stamp_duty_rate=stamp_duty_rate,
            slippage_rate=slippage_rate,
            warmup_bars=warmup_bars,
            score_lookups=training_score_lookups,
        )
        comparisons[threshold] = comparison
        summary = comparison.aggregates[strategy_name].summary
        eligible = summary.trade_count >= minimum_training_trades
        candidates.append(ThresholdCandidate(
            threshold=threshold,
            eligible=eligible,
            selection_score=_selection_score(summary) if eligible else None,
            training_summary=summary,
        ))

    eligible_candidates = [candidate for candidate in candidates if candidate.eligible]
    if not eligible_candidates:
        raise ValueError(
            f"所有候选阈值的训练交易数都少于 {minimum_training_trades}，拒绝选择参数"
        )
    selected = max(
        eligible_candidates,
        key=lambda item: (
            item.selection_score if item.selection_score is not None else -inf,
            item.training_summary.trade_count,
            item.threshold,
        ),
    )
    validation = compare_strategies(
        validation_bars,
        {strategy_name: scorer},
        horizon=horizon,
        buy_threshold=selected.threshold,
        commission_rate=commission_rate,
        stamp_duty_rate=stamp_duty_rate,
        slippage_rate=slippage_rate,
        warmup_bars=warmup_bars,
        evaluation_start=validation_start,
        evaluation_end=validation_end,
        score_lookups=validation_score_lookups,
    )
    return WalkForwardResult(
        strategy_name=strategy_name,
        horizon=horizon,
        train_start=train_start,
        train_end=train_end,
        validation_start=validation_start,
        validation_end=validation_end,
        minimum_training_trades=minimum_training_trades,
        selected_threshold=selected.threshold,
        candidates=tuple(candidates),
        training=comparisons[selected.threshold],
        validation=validation,
    )

"""Cross-sectional stock-selection candidate for local research.

The scorer only uses bars available through the signal date.  It deliberately
does not perform external I/O or market-state adjustments so it can be reused
by both the portfolio backtester and an eventual read-only recommendation
view.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from math import isfinite, sqrt
from typing import Optional

import pandas as pd


FACTOR_WEIGHTS = {
    "trend": 0.35,
    "momentum_60_skip5": 0.35,
    "momentum_20_skip5": 0.15,
    "volume_confirmation": 0.10,
    "low_volatility": 0.05,
}


@dataclass(frozen=True)
class SelectionConfig:
    """Configuration for the research-only cross-sectional selector."""

    lookback_bars: int = 126
    top_n: int = 3
    rebalance_every: int = 5
    min_price: float = 1.0
    min_amount: float = 50_000_000.0
    max_volatility_pct: float = 100.0
    max_drawdown_pct: float = -50.0

    def validate(self) -> None:
        if self.lookback_bars < 66:
            raise ValueError("lookback_bars 不能小于 66")
        if self.top_n < 1:
            raise ValueError("top_n 必须大于 0")
        if self.rebalance_every < 1:
            raise ValueError("rebalance_every 必须大于 0")
        if self.min_price < 0 or self.min_amount < 0:
            raise ValueError("价格和成交额下限不能为负数")
        if self.max_volatility_pct <= 0:
            raise ValueError("max_volatility_pct 必须大于 0")
        if not -100 <= self.max_drawdown_pct <= 0:
            raise ValueError("max_drawdown_pct 必须在 -100 到 0 之间")


@dataclass(frozen=True)
class CandidateScore:
    stock_code: str
    trade_date: date
    score: float
    rank: int
    factors: dict[str, float]
    factor_ranks: dict[str, float]
    reason: str
    rejection_reason: Optional[str] = None

    def to_dict(self) -> dict:
        result = asdict(self)
        result["trade_date"] = self.trade_date.isoformat()
        return result


REQUIRED_COLUMNS = {"trade_date", "open", "high", "low", "close", "volume"}
OPTIONAL_COLUMNS = {"amount"}


def prepare_bars(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalize one stock's bars without reading data outside ``frame``."""
    missing = REQUIRED_COLUMNS - set(frame.columns)
    if missing:
        raise ValueError(f"横截面策略缺少字段: {', '.join(sorted(missing))}")

    bars = frame.copy()
    bars["trade_date"] = pd.to_datetime(bars["trade_date"], errors="coerce").dt.date
    for column in ("open", "high", "low", "close", "volume"):
        bars[column] = pd.to_numeric(bars[column], errors="coerce")
    if "amount" in bars.columns:
        bars["amount"] = pd.to_numeric(bars["amount"], errors="coerce")
    else:
        bars["amount"] = 0.0

    bars = bars.dropna(subset=list(REQUIRED_COLUMNS))
    bars = bars.sort_values("trade_date")
    bars = bars.drop_duplicates(subset=["trade_date"], keep="last")
    bars = bars.reset_index(drop=True)
    return bars


def _finite(value: float) -> bool:
    return isfinite(float(value))


def compute_raw_factors(history: pd.DataFrame,
                        config: SelectionConfig = SelectionConfig()) -> dict:
    """Compute raw factors for one stock at the last row of ``history``.

    The recent five bars are excluded from the medium and short momentum
    factors.  This separates medium-term continuation from the short-term
    reversal component that weakened the previous V3 score.
    """
    config.validate()
    bars = prepare_bars(history)
    if len(bars) < config.lookback_bars:
        return {
            "eligible": False,
            "rejection_reason": f"有效日线不足 {config.lookback_bars} 根",
        }

    window = bars.tail(config.lookback_bars).reset_index(drop=True)
    close = float(window["close"].iloc[-1])
    volume = float(window["volume"].iloc[-1])
    amount = float(window["amount"].iloc[-1])
    if close < config.min_price:
        return {"eligible": False, "rejection_reason": "价格低于可交易下限"}
    if volume <= 0 or amount < config.min_amount:
        return {"eligible": False, "rejection_reason": "成交量或成交额不足"}

    closes = window["close"]
    volumes = window["volume"]
    ma20 = float(closes.tail(20).mean())
    ma60 = float(closes.tail(60).mean())
    ma120 = float(closes.tail(120).mean())
    trend = ((close / ma20 - 1) + (ma20 / ma60 - 1) + (close / ma120 - 1)) / 3 * 100

    # Signal at t can use t-5 and t-65, but never t+1 or later.
    momentum_60_skip5 = (float(closes.iloc[-6]) / float(closes.iloc[-66]) - 1) * 100
    momentum_20_skip5 = (float(closes.iloc[-6]) / float(closes.iloc[-26]) - 1) * 100

    recent_volume = float(volumes.tail(5).mean())
    previous_volume = float(volumes.iloc[-25:-5].mean())
    volume_ratio = recent_volume / previous_volume if previous_volume > 0 else 1.0
    recent_return = close / float(closes.iloc[-6]) - 1
    volume_confirmation = (1 if recent_return >= 0 else -1) * (volume_ratio - 1) * 100

    returns = closes.pct_change().dropna()
    volatility_pct = float(returns.tail(60).std(ddof=0) * sqrt(252) * 100)
    drawdown_pct = float((close / float(closes.tail(60).max()) - 1) * 100)
    if not all(_finite(value) for value in (
        trend, momentum_60_skip5, momentum_20_skip5,
        volume_confirmation, volatility_pct, drawdown_pct,
    )):
        return {"eligible": False, "rejection_reason": "因子计算结果无效"}
    if volatility_pct > config.max_volatility_pct:
        return {"eligible": False, "rejection_reason": "60 日年化波动率过高"}
    if drawdown_pct < config.max_drawdown_pct:
        return {"eligible": False, "rejection_reason": "60 日回撤超过限制"}

    return {
        "eligible": True,
        "trade_date": window["trade_date"].iloc[-1],
        "close": close,
        "amount": amount,
        "trend": trend,
        "momentum_60_skip5": momentum_60_skip5,
        "momentum_20_skip5": momentum_20_skip5,
        "volume_confirmation": volume_confirmation,
        "low_volatility": -volatility_pct,
        "volatility_pct": volatility_pct,
        "drawdown_pct": drawdown_pct,
    }


def rank_raw_factors(raw_factors: dict[str, dict],
                     trade_date: date,
                     top_n: Optional[int] = None) -> list[CandidateScore]:
    """Rank eligible raw factor rows on one signal date."""
    rows = {
        code: values for code, values in raw_factors.items()
        if values.get("eligible")
    }
    if not rows:
        return []

    factor_columns = list(FACTOR_WEIGHTS)
    frame = pd.DataFrame.from_dict(rows, orient="index")
    ranks = frame[factor_columns].rank(method="average", pct=True) * 100
    results = []
    for code in frame.index:
        factor_ranks = {
            column: round(float(ranks.loc[code, column]), 4)
            for column in factor_columns
        }
        score = sum(
            factor_ranks[column] * weight
            for column, weight in FACTOR_WEIGHTS.items()
        )
        strongest = sorted(
            factor_ranks.items(), key=lambda item: item[1], reverse=True
        )[:2]
        reason = "；".join(
            f"{column} 排名 {value:.1f}%" for column, value in strongest
        )
        results.append(CandidateScore(
            stock_code=code,
            trade_date=trade_date,
            score=round(float(score), 4),
            rank=0,
            factors={
                column: round(float(frame.loc[code, column]), 6)
                for column in factor_columns
            },
            factor_ranks=factor_ranks,
            reason=reason,
        ))

    results.sort(key=lambda item: (-item.score, item.stock_code))
    limited = results if top_n is None else results[:top_n]
    return [
        CandidateScore(
            stock_code=item.stock_code,
            trade_date=item.trade_date,
            score=item.score,
            rank=index,
            factors=item.factors,
            factor_ranks=item.factor_ranks,
            reason=item.reason,
        )
        for index, item in enumerate(limited, start=1)
    ]


def rank_universe_on_date(
    bars_by_stock: dict[str, pd.DataFrame],
    trade_date: date,
    config: SelectionConfig = SelectionConfig(),
    top_n: Optional[int] = None,
) -> list[CandidateScore]:
    """Compute a date-aligned ranked candidate list for one universe."""
    config.validate()
    if top_n is None:
        top_n = config.top_n
    raw = {}
    for code, frame in bars_by_stock.items():
        bars = frame[frame["trade_date"] <= trade_date]
        values = compute_raw_factors(bars, config)
        if values.get("eligible"):
            if values.get("trade_date") != trade_date:
                values = {
                    "eligible": False,
                    "rejection_reason": "股票最新日线与信号日不对齐",
                }
        raw[str(code).zfill(6)] = values
    return rank_raw_factors(raw, trade_date, top_n=top_n)

"""Explainable multi-factor strategy candidate for research and calibration."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from bisect import bisect_right
from typing import Optional

import pandas as pd


@dataclass(frozen=True)
class FactorContribution:
    name: str
    score: float
    max_score: float
    values: dict[str, float]
    reason: str


@dataclass(frozen=True)
class RiskPlan:
    atr_pct: float
    stop_loss_pct: float
    take_profit_pct: float
    trailing_stop_pct: float


@dataclass(frozen=True)
class StrategyEvaluation:
    score: int
    recommendation: str
    confidence: str
    data_date: Optional[date]
    factors: tuple[FactorContribution, ...]
    risk: RiskPlan

    def to_dict(self) -> dict:
        result = asdict(self)
        result["data_date"] = self.data_date.isoformat() if self.data_date else None
        return result


def _prepare_bars(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"open", "high", "low", "close", "volume"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"V3 策略缺少字段: {', '.join(sorted(missing))}")
    bars = frame.copy()
    for column in required:
        bars[column] = pd.to_numeric(bars[column], errors="coerce")
    if "trade_date" in bars:
        bars["trade_date"] = pd.to_datetime(bars["trade_date"], errors="coerce").dt.date
    bars = bars.dropna(subset=list(required)).reset_index(drop=True)
    if len(bars) < 60:
        raise ValueError("V3 策略至少需要 60 根有效日线")
    return bars


def _rsi(closes: pd.Series, period: int = 14) -> float:
    delta = closes.diff()
    gains = delta.clip(lower=0).rolling(period).mean()
    losses = (-delta.clip(upper=0)).rolling(period).mean()
    gain = float(gains.iloc[-1])
    loss = float(losses.iloc[-1])
    if loss == 0:
        return 100.0 if gain > 0 else 50.0
    return 100 - 100 / (1 + gain / loss)


def _atr(bars: pd.DataFrame, period: int = 14) -> float:
    previous_close = bars["close"].shift(1)
    true_range = pd.concat([
        bars["high"] - bars["low"],
        (bars["high"] - previous_close).abs(),
        (bars["low"] - previous_close).abs(),
    ], axis=1).max(axis=1)
    return float(true_range.rolling(period).mean().iloc[-1])


def _trend_factor(bars: pd.DataFrame) -> FactorContribution:
    close = float(bars["close"].iloc[-1])
    ma20_series = bars["close"].rolling(20).mean()
    ma20 = float(ma20_series.iloc[-1])
    ma60 = float(bars["close"].rolling(60).mean().iloc[-1])
    ma20_slope = (ma20 / float(ma20_series.iloc[-6]) - 1) * 100
    high20 = float(bars["high"].tail(20).max())
    distance_high = (close / high20 - 1) * 100

    score = 0.0
    reasons = []
    if close > ma20:
        score += 8
        reasons.append("价格站上20日均线")
    if ma20 > ma60:
        score += 8
        reasons.append("中期均线多头排列")
    if ma20_slope > 0:
        score += 4
        reasons.append("20日均线向上")
    if distance_high >= -3:
        score += 5
        reasons.append("接近20日新高")
    return FactorContribution(
        "趋势", score, 25.0,
        {"close": close, "ma20": ma20, "ma60": ma60, "ma20_slope_pct": ma20_slope},
        "；".join(reasons) or "趋势偏弱",
    )


def _momentum_factor(bars: pd.DataFrame) -> FactorContribution:
    closes = bars["close"]
    rsi = _rsi(closes)
    roc20 = (float(closes.iloc[-1]) / float(closes.iloc[-21]) - 1) * 100
    ema12 = closes.ewm(span=12, adjust=False).mean()
    ema26 = closes.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    macd_hist = float((dif - dea).iloc[-1])

    score = 0.0
    reasons = []
    if 45 <= rsi <= 65:
        score += 8
        reasons.append("RSI处于健康动量区")
    elif 30 <= rsi < 45 or 65 < rsi <= 75:
        score += 5
    elif rsi < 30:
        score += 3
        reasons.append("RSI超卖但动量尚未确认")
    else:
        reasons.append("RSI过热")

    if 0 < roc20 <= 15:
        score += 7
        reasons.append("20日动量温和向上")
    elif roc20 > 15:
        score += 4
        reasons.append("20日涨幅较大")
    if macd_hist > 0:
        score += 5
        reasons.append("MACD动能为正")
    return FactorContribution(
        "动量", score, 20.0,
        {"rsi14": rsi, "roc20_pct": roc20, "macd_hist": macd_hist},
        "；".join(reasons) or "动量偏弱",
    )


def _mean_reversion_factor(bars: pd.DataFrame) -> FactorContribution:
    close = float(bars["close"].iloc[-1])
    closes20 = bars["close"].tail(20)
    mean20 = float(closes20.mean())
    std20 = float(closes20.std(ddof=0))
    zscore = (close - mean20) / std20 if std20 > 0 else 0.0
    if -1.5 <= zscore <= -0.2:
        score, reason = 15.0, "温和回调，价格具有均值回归空间"
    elif -2.5 <= zscore < -1.5:
        score, reason = 10.0, "明显超跌，需等待趋势确认"
    elif -0.2 < zscore <= 1.0:
        score, reason = 9.0, "价格处于正常区间"
    elif 1.0 < zscore <= 2.0:
        score, reason = 4.0, "价格偏离均值较高"
    else:
        score, reason = 1.0, "价格极端偏离均值"
    return FactorContribution(
        "均值回归", score, 15.0,
        {"zscore20": zscore, "mean20": mean20}, reason,
    )


def _volume_price_factor(bars: pd.DataFrame) -> FactorContribution:
    volume5 = float(bars["volume"].tail(5).mean())
    volume20 = float(bars["volume"].tail(20).mean())
    volume_ratio = volume5 / volume20 if volume20 > 0 else 1.0
    return5 = (float(bars["close"].iloc[-1]) / float(bars["close"].iloc[-6]) - 1) * 100
    if return5 > 0 and volume_ratio >= 1.2:
        score, reason = 15.0, "上涨伴随成交量扩张"
    elif return5 > 0 and volume_ratio >= 0.8:
        score, reason = 11.0, "价格上涨且量能正常"
    elif return5 < 0 and volume_ratio >= 1.3:
        score, reason = 1.0, "放量下跌，资金撤离风险"
    elif return5 < 0 and volume_ratio < 0.8:
        score, reason = 8.0, "缩量回调"
    else:
        score, reason = 6.0, "量价关系中性"
    return FactorContribution(
        "量价", score, 15.0,
        {"volume_ratio_5_20": volume_ratio, "return5_pct": return5}, reason,
    )


def _volatility_factor(bars: pd.DataFrame) -> tuple[FactorContribution, RiskPlan]:
    close = float(bars["close"].iloc[-1])
    atr = _atr(bars)
    atr_pct = atr / close * 100 if close > 0 else 0.0
    if 1 <= atr_pct <= 4:
        score, reason = 10.0, "波动率适中"
    elif atr_pct < 1:
        score, reason = 7.0, "波动较低"
    elif atr_pct <= 7:
        score, reason = 5.0, "波动偏高"
    else:
        score, reason = 1.0, "波动过高，风险较大"
    stop = min(10.0, max(3.0, atr_pct * 2))
    risk = RiskPlan(
        atr_pct=round(atr_pct, 4),
        stop_loss_pct=round(-stop, 2),
        take_profit_pct=round(stop * 2, 2),
        trailing_stop_pct=round(-min(8.0, max(2.0, atr_pct * 1.5)), 2),
    )
    factor = FactorContribution("波动风险", score, 10.0, {"atr14_pct": atr_pct}, reason)
    return factor, risk


def _neutral_market_factor() -> FactorContribution:
    return FactorContribution("市场环境", 7.0, 15.0, {}, "缺少指数环境数据，按中性处理")


def _market_factor_prepared(bars: pd.DataFrame) -> FactorContribution:
    if len(bars) < 60:
        return _neutral_market_factor()
    close = float(bars["close"].iloc[-1])
    ma20 = float(bars["close"].tail(20).mean())
    ma60 = float(bars["close"].tail(60).mean())
    if close > ma20 > ma60:
        score, reason = 15.0, "市场处于多头环境"
    elif close < ma20 < ma60:
        score, reason = 0.0, "市场处于空头环境"
    elif close > ma20:
        score, reason = 10.0, "市场短期偏强"
    else:
        score, reason = 5.0, "市场环境中性偏弱"
    return FactorContribution(
        "市场环境", score, 15.0,
        {"close": close, "ma20": ma20, "ma60": ma60}, reason,
    )


def _market_factor(market_bars: Optional[pd.DataFrame]) -> FactorContribution:
    if market_bars is None or len(market_bars) < 60:
        return _neutral_market_factor()
    return _market_factor_prepared(_prepare_bars(market_bars))


def _evaluate_prepared(
    bars: pd.DataFrame,
    market: FactorContribution,
) -> StrategyEvaluation:
    trend = _trend_factor(bars)
    momentum = _momentum_factor(bars)
    mean_reversion = _mean_reversion_factor(bars)
    volume_price = _volume_price_factor(bars)
    volatility, risk = _volatility_factor(bars)
    factors = (trend, momentum, mean_reversion, volume_price, volatility, market)
    score = int(round(sum(factor.score for factor in factors)))
    if score >= 70:
        recommendation = "候选买入"
    elif score <= 35:
        recommendation = "回避"
    else:
        recommendation = "观察"
    if len(bars) >= 120 and market.values:
        confidence = "高"
    elif len(bars) >= 90:
        confidence = "中"
    else:
        confidence = "低"
    data_date = bars.iloc[-1].get("trade_date") if "trade_date" in bars else None
    return StrategyEvaluation(score, recommendation, confidence, data_date, factors, risk)


def evaluate_strategy_v3(frame: pd.DataFrame,
                         market_bars: Optional[pd.DataFrame] = None) -> StrategyEvaluation:
    """Evaluate one historical window without external I/O."""
    bars = _prepare_bars(frame)
    market = _market_factor(market_bars)
    return _evaluate_prepared(bars, market)


def score_strategy_v3(history: pd.DataFrame, _current_price: float,
                      _change_pct: float) -> tuple[int, str]:
    """Adapter matching the baseline backtest scorer signature."""
    evaluation = evaluate_strategy_v3(history)
    return _format_score(evaluation)


def _format_score(evaluation: StrategyEvaluation) -> tuple[int, str]:
    strongest = sorted(evaluation.factors, key=lambda item: item.score / item.max_score,
                       reverse=True)[:3]
    reason = "；".join(f"{factor.name}: {factor.reason}" for factor in strongest)
    return evaluation.score, reason


def make_strategy_v3_scorer(market_bars: pd.DataFrame):
    """Create a scorer that can only see market bars available on the signal date."""
    market = _prepare_bars(market_bars)
    if "trade_date" not in market:
        raise ValueError("市场环境数据必须包含 trade_date")
    market = market.sort_values("trade_date").reset_index(drop=True)
    market_dates = market["trade_date"].tolist()
    market_factors = [
        _market_factor_prepared(market.iloc[max(0, index - 119):index + 1])
        for index in range(len(market))
    ]

    def scorer(history: pd.DataFrame, _current_price: float,
               _change_pct: float) -> tuple[int, str]:
        history_date = pd.to_datetime(
            history.iloc[-1].get("trade_date"), errors="coerce"
        )
        if pd.isna(history_date):
            evaluation = evaluate_strategy_v3(history)
        else:
            cutoff = bisect_right(market_dates, history_date.date())
            factor = market_factors[cutoff - 1] if cutoff else _neutral_market_factor()
            # Backtest history is already normalized by backtest_engine.
            evaluation = _evaluate_prepared(history, factor)
        return _format_score(evaluation)

    return scorer

"""Simple research baselines that candidate strategies must outperform."""

from __future__ import annotations

import pandas as pd


def score_ma_trend(history: pd.DataFrame, _current_price: float,
                   _change_pct: float) -> tuple[int, str]:
    """Signal when close > MA20 > MA60; use the shared next-open backtester."""
    closes = pd.to_numeric(history["close"], errors="coerce").dropna()
    if len(closes) < 60:
        raise ValueError("均线基准至少需要 60 根日线")
    close = float(closes.iloc[-1])
    ma20 = float(closes.tail(20).mean())
    ma60 = float(closes.tail(60).mean())
    if close > ma20 > ma60:
        return 80, "价格位于 MA20 和 MA60 上方，均线多头排列"
    return 50, "未形成 close > MA20 > MA60"

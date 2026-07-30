"""Read-only dashboard queries; no external market requests are allowed here."""

from __future__ import annotations

from datetime import datetime
from math import sqrt
from pathlib import Path

import pandas as pd
from sqlalchemy import func

from database import (
    DATABASE_URL,
    DailyBar,
    MarketQuoteSnapshot,
    Portfolio,
    QuoteSnapshot,
    Watchlist,
    get_db,
    init_db,
)
from global_market_data import (
    global_markets_enabled,
    market_definitions,
    market_poll_interval,
)
from research_universe import RESEARCH_UNIVERSE
from settings import get_owner_user_id
from strategies import calculate_score_from_history


def _snapshot_payload(snapshot: QuoteSnapshot, stale_after_seconds: float) -> dict:
    age_seconds = max(0.0, (datetime.now() - snapshot.quote_at).total_seconds())
    return {
        "price": snapshot.price,
        "change_pct": snapshot.change_pct,
        "score": snapshot.score,
        "reason": snapshot.reason or "暂无评分说明",
        "quote_at": snapshot.quote_at,
        "age_seconds": age_seconds,
        "stale": age_seconds > stale_after_seconds,
    }


def load_overview(config: dict) -> dict:
    init_db()
    owner_user_id = get_owner_user_id(config)
    interval = float(config.get("monitor", {}).get("interval_seconds", 30))
    stale_after = max(120.0, interval * 3)
    db = get_db()
    try:
        if owner_user_id is not None:
            portfolio_records = db.query(Portfolio).filter(
                Portfolio.user_id == owner_user_id
            ).order_by(Portfolio.stock_code).all()
            watch_records = db.query(Watchlist).filter(
                Watchlist.user_id == owner_user_id
            ).order_by(Watchlist.stock_code).all()
            portfolio = [
                {
                    "code": row.stock_code,
                    "name": row.name,
                    "buy_price": row.buy_price,
                    "shares": row.shares,
                    "stop_loss": row.stop_loss,
                    "take_profit": row.take_profit,
                }
                for row in portfolio_records
            ]
            watchlist = [
                {"code": row.stock_code, "name": row.name}
                for row in watch_records
            ]
        else:
            portfolio = [
                {"code": code, **holding}
                for code, holding in (config.get("portfolio", {}) or {}).items()
            ]
            watchlist = [
                {"code": code, "name": name}
                for code, name in (config.get("watchlist", {}) or {}).items()
            ]

        codes = {row["code"] for row in portfolio + watchlist}
        snapshots = {
            row.stock_code: row
            for row in db.query(QuoteSnapshot).filter(
                QuoteSnapshot.stock_code.in_(codes)
            ).all()
        } if codes else {}

        total_cost = 0.0
        total_value = 0.0
        for row in portfolio:
            snapshot = snapshots.get(row["code"])
            row["snapshot"] = (
                _snapshot_payload(snapshot, stale_after) if snapshot else None
            )
            cost = float(row.get("buy_price", 0)) * int(row.get("shares", 0))
            value = (
                snapshot.price * int(row.get("shares", 0)) if snapshot else cost
            )
            row["profit_amount"] = value - cost if snapshot else None
            row["profit_pct"] = (value / cost - 1) * 100 if snapshot and cost else None
            total_cost += cost
            total_value += value

        for row in watchlist:
            snapshot = snapshots.get(row["code"])
            row["snapshot"] = (
                _snapshot_payload(snapshot, stale_after) if snapshot else None
            )

        quote_times = [row.quote_at for row in snapshots.values()]
        return {
            "portfolio": portfolio,
            "watchlist": watchlist,
            "total_cost": total_cost,
            "total_value": total_value,
            "total_profit": total_value - total_cost,
            "total_profit_pct": (
                (total_value / total_cost - 1) * 100 if total_cost else 0.0
            ),
            "latest_quote_at": max(quote_times) if quote_times else None,
            "stale_after_seconds": stale_after,
        }
    finally:
        db.close()


def _market_snapshot_payload(
    snapshot: MarketQuoteSnapshot,
    stale_after_seconds: float,
) -> dict:
    age_seconds = max(0.0, (datetime.now() - snapshot.quote_at).total_seconds())
    return {
        "price": snapshot.price,
        "change_pct": snapshot.change_pct,
        "currency": snapshot.currency,
        "quote_at": snapshot.quote_at,
        "market_at": snapshot.market_at,
        "source": snapshot.source,
        "age_seconds": age_seconds,
        "stale": age_seconds > stale_after_seconds,
    }


def load_market_overview(config: dict) -> dict:
    """Load configured cross-market index snapshots without network access."""

    definitions = market_definitions(config)
    enabled = global_markets_enabled(config)
    stale_after = max(900.0, market_poll_interval(config) * 3)
    if not definitions:
        return {
            "enabled": enabled,
            "markets": [],
            "latest_quote_at": None,
            "stale_after_seconds": stale_after,
            "source": "Yahoo Finance Chart API",
        }

    init_db()
    db = get_db()
    try:
        snapshots = {
            (row.market, row.symbol): row
            for row in db.query(MarketQuoteSnapshot).all()
        }
    finally:
        db.close()

    grouped: dict[str, dict] = {}
    all_quote_times = []
    for definition in definitions:
        market = grouped.setdefault(
            definition.market,
            {
                "key": definition.market,
                "name": definition.market_name,
                "currency": definition.currency,
                "timezone": definition.timezone,
                "indices": [],
            },
        )
        snapshot = snapshots.get((definition.market, definition.symbol))
        payload = (
            _market_snapshot_payload(snapshot, stale_after)
            if snapshot
            else None
        )
        if snapshot:
            all_quote_times.append(snapshot.quote_at)
        market["indices"].append(
            {
                "symbol": definition.symbol,
                "name": snapshot.name if snapshot else definition.name,
                "snapshot": payload,
            }
        )

    for market in grouped.values():
        market_times = [
            index["snapshot"]["quote_at"]
            for index in market["indices"]
            if index["snapshot"]
        ]
        market["latest_quote_at"] = max(market_times) if market_times else None

    return {
        "enabled": enabled,
        "markets": list(grouped.values()),
        "latest_quote_at": max(all_quote_times) if all_quote_times else None,
        "stale_after_seconds": stale_after,
        "source": "Yahoo Finance Chart API",
    }


def load_stock_summary(stock_code: str) -> dict:
    init_db()
    db = get_db()
    try:
        snapshot = db.get(QuoteSnapshot, stock_code)
        return {
            "code": stock_code,
            "name": snapshot.name if snapshot else stock_code,
            "snapshot": _snapshot_payload(snapshot, 120) if snapshot else None,
        }
    finally:
        db.close()


def _personal_codes(db, config: dict) -> tuple[set[str], set[str], dict[str, str]]:
    """Return portfolio/watchlist membership without making external requests."""
    owner_user_id = get_owner_user_id(config)
    names: dict[str, str] = {}
    if owner_user_id is not None:
        portfolios = db.query(Portfolio).filter(
            Portfolio.user_id == owner_user_id
        ).all()
        watches = db.query(Watchlist).filter(
            Watchlist.user_id == owner_user_id
        ).all()
        portfolio_codes = {row.stock_code for row in portfolios}
        watchlist_codes = {row.stock_code for row in watches}
        names.update({row.stock_code: row.name for row in portfolios})
        names.update({row.stock_code: row.name for row in watches})
    else:
        portfolio = config.get("portfolio", {}) or {}
        watchlist = config.get("watchlist", {}) or {}
        portfolio_codes = set(portfolio)
        watchlist_codes = set(watchlist)
        names.update(
            {
                code: holding.get("name", code)
                for code, holding in portfolio.items()
            }
        )
        names.update({code: name for code, name in watchlist.items()})
    return portfolio_codes, watchlist_codes, names


def _candidate_label(score: int, momentum_20: float, above_ma20: bool) -> str:
    if score >= 70 and momentum_20 > 0 and above_ma20:
        return "重点研究"
    if score >= 60 and momentum_20 >= -5 and above_ma20:
        return "进入观察"
    if score >= 45:
        return "耐心等待"
    return "风险回避"


def _candidate_explanation(candidate: dict) -> tuple[list[str], list[str]]:
    reasons = [f"V2 技术评分 {candidate['score']} 分"]
    if candidate["above_ma20"]:
        reasons.append("收盘价站上 20 日均线")
    if candidate["momentum_20"] > 0:
        reasons.append(f"20 日动量为 {candidate['momentum_20']:+.1f}%")
    if candidate["volume_ratio"] >= 1.2:
        reasons.append(f"近期量能为前期的 {candidate['volume_ratio']:.2f} 倍")

    objections = []
    if not candidate["above_ma20"]:
        objections.append("仍在 20 日均线下方，趋势尚未确认")
    if candidate["momentum_20"] < 0:
        objections.append(f"20 日动量为 {candidate['momentum_20']:+.1f}%")
    if candidate["volatility"] > 45:
        objections.append(f"年化波动率 {candidate['volatility']:.1f}%，波动偏高")
    if candidate["drawdown_60"] < -15:
        objections.append(f"较 60 日高点回撤 {candidate['drawdown_60']:.1f}%")
    if candidate["volume_ratio"] < 0.7:
        objections.append("近期量能明显收缩")
    if not objections:
        objections.append("技术面没有明显反对项，但不包含基本面与突发事件")
    return reasons[:3], objections[:3]


def _research_candidates(config: dict) -> dict:
    """Compute candidates from one aligned local qfq trading date."""
    init_db()
    db = get_db()
    try:
        latest_date = db.query(func.max(DailyBar.trade_date)).filter(
            DailyBar.adjust == "qfq",
            DailyBar.stock_code.in_(RESEARCH_UNIVERSE),
        ).scalar()
        if latest_date is None:
            return {
                "as_of": None,
                "candidates": [],
                "universe_size": len(RESEARCH_UNIVERSE),
                "eligible_size": 0,
            }

        rows = db.query(DailyBar).filter(
            DailyBar.adjust == "qfq",
            DailyBar.stock_code.in_(RESEARCH_UNIVERSE),
            DailyBar.trade_date <= latest_date,
        ).order_by(DailyBar.stock_code, DailyBar.trade_date).all()
        latest_codes = {
            row.stock_code for row in rows if row.trade_date == latest_date
        }
        portfolio_codes, watchlist_codes, personal_names = _personal_codes(db, config)
        snapshot_names = {
            row.stock_code: row.name
            for row in db.query(QuoteSnapshot).filter(
                QuoteSnapshot.stock_code.in_(RESEARCH_UNIVERSE)
            ).all()
        }
    finally:
        db.close()

    grouped: dict[str, list[DailyBar]] = {}
    for row in rows:
        if row.stock_code in latest_codes:
            grouped.setdefault(row.stock_code, []).append(row)

    candidates = []
    for code, code_rows in grouped.items():
        window = code_rows[-60:]
        if len(window) < 60:
            continue
        closes = pd.Series([float(row.close) for row in window], dtype="float64")
        volumes = pd.Series([float(row.volume) for row in window], dtype="float64")
        if (closes <= 0).any() or len(closes) < 21:
            continue
        change_pct = float((closes.iloc[-1] / closes.iloc[-2] - 1) * 100)
        history = pd.DataFrame({"收盘": closes, "成交量": volumes})
        score, score_reason = calculate_score_from_history(
            history, float(closes.iloc[-1]), change_pct
        )
        ma20 = float(closes.tail(20).mean())
        momentum_20 = float((closes.iloc[-1] / closes.iloc[-21] - 1) * 100)
        previous_volume = float(volumes.iloc[-10:-5].mean())
        recent_volume = float(volumes.iloc[-5:].mean())
        volume_ratio = recent_volume / previous_volume if previous_volume > 0 else 1.0
        returns = closes.pct_change().dropna()
        volatility = float(returns.std(ddof=0) * sqrt(252) * 100)
        drawdown_60 = float((closes.iloc[-1] / closes.max() - 1) * 100)
        candidate = {
            "code": code,
            "name": personal_names.get(code)
            or snapshot_names.get(code)
            or RESEARCH_UNIVERSE[code],
            "close": float(closes.iloc[-1]),
            "change_pct": change_pct,
            "score": score,
            "score_reason": score_reason,
            "ma20": ma20,
            "above_ma20": bool(closes.iloc[-1] > ma20),
            "momentum_20": momentum_20,
            "volume_ratio": float(volume_ratio),
            "volatility": volatility,
            "drawdown_60": drawdown_60,
            "invalidation_price": float(closes.tail(20).min()),
            "is_portfolio": code in portfolio_codes,
            "is_watchlist": code in watchlist_codes,
        }
        candidate["label"] = _candidate_label(score, momentum_20, candidate["above_ma20"])
        candidate["reasons"], candidate["objections"] = _candidate_explanation(candidate)
        candidates.append(candidate)

    label_order = {"重点研究": 0, "进入观察": 1, "耐心等待": 2, "风险回避": 3}
    candidates.sort(
        key=lambda row: (
            label_order[row["label"]],
            -row["score"],
            -row["momentum_20"],
            row["code"],
        )
    )
    return {
        "as_of": latest_date,
        "candidates": candidates,
        "universe_size": len(RESEARCH_UNIVERSE),
        "eligible_size": len(candidates),
    }


def load_recommendations(config: dict, limit: int = 12) -> dict:
    """Return explainable, locally computed research candidates."""
    data = _research_candidates(config)
    data["candidates"] = data["candidates"][:limit]
    data["model"] = "V2 技术评分 + 趋势/风险过滤"
    return data


def load_screener(
    config: dict,
    *,
    min_score: int = 50,
    min_momentum: float = -100,
    max_volatility: float = 100,
    above_ma20: bool = False,
    limit: int = 20,
) -> dict:
    """Filter the aligned local research universe with reproducible criteria."""
    data = _research_candidates(config)
    matches = [
        row
        for row in data["candidates"]
        if row["score"] >= min_score
        and row["momentum_20"] >= min_momentum
        and row["volatility"] <= max_volatility
        and (not above_ma20 or row["above_ma20"])
    ]
    data["candidates"] = matches[:limit]
    data["match_count"] = len(matches)
    data["filters"] = {
        "min_score": min_score,
        "min_momentum": min_momentum,
        "max_volatility": max_volatility,
        "above_ma20": above_ma20,
        "limit": limit,
    }
    return data


def load_system_status() -> dict:
    init_db()
    db = get_db()
    try:
        bar_count = db.query(func.count(DailyBar.id)).scalar() or 0
        stock_count = db.query(func.count(func.distinct(DailyBar.stock_code))).scalar() or 0
        latest_bar = db.query(func.max(DailyBar.trade_date)).scalar()
        snapshot_count = db.query(func.count(QuoteSnapshot.stock_code)).scalar() or 0
        latest_quote = db.query(func.max(QuoteSnapshot.quote_at)).scalar()
    finally:
        db.close()
    db_size = None
    prefix = "sqlite:///"
    if DATABASE_URL.startswith(prefix):
        path = Path(DATABASE_URL[len(prefix):])
        if path.exists():
            db_size = path.stat().st_size
    return {
        "bar_count": bar_count,
        "stock_count": stock_count,
        "latest_bar": latest_bar,
        "snapshot_count": snapshot_count,
        "latest_quote": latest_quote,
        "database_size": db_size,
    }

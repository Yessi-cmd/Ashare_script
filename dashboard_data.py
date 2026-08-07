"""Read-only dashboard queries; no external market requests are allowed here."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from math import sqrt
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import func
from sqlalchemy.orm import aliased

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

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")

# 各市场本地时间的交易时段窗口。闭市期间“最新收盘”仍是有效数据，
# 避免美股/日韩指数在亚盘时段被误标为过期。
_MARKET_SESSION_WINDOWS = {
    "a_share": ("09:30", "16:00"),
    "hk": ("09:30", "16:00"),
    "kr": ("09:00", "15:30"),
    "jp": ("09:00", "15:30"),
    "us": ("09:30", "16:00"),
}
# 闭市期间把快照视为过期的年龄上限，作为采集器长时间停摆的兜底（覆盖长周末）。
MARKET_CLOSED_STALE_SECONDS = 7 * 24 * 60 * 60

# 荐股/选股只依赖本地日线与个人持仓关系；日线在收盘后同步一次，
# 因此以最近对齐交易日为缓存键，避免每个页面请求都全量重算。
CANDIDATES_CACHE_TTL_SECONDS = 60.0
_candidates_cache: dict = {}
_candidates_cache_lock = threading.Lock()


def _utc_now() -> datetime:
    """当前 UTC 时间（naive），用于与以 UTC 存储的快照时间比较。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _to_local_display(value: datetime | None, tz_name: str) -> datetime | None:
    """把 UTC 存储时间转换为指定时区的展示时间（naive）。"""
    if value is None:
        return None
    try:
        target_tz = ZoneInfo(tz_name)
    except (KeyError, ValueError, OSError):
        target_tz = SHANGHAI_TZ
    return value.replace(tzinfo=timezone.utc).astimezone(target_tz).replace(tzinfo=None)


def _session_bound(spec: str, day: datetime) -> datetime:
    hour, minute = spec.split(":")
    return day.replace(hour=int(hour), minute=int(minute), second=0, microsecond=0)


def _market_is_open(market: str, tz_name: str, now_utc: datetime) -> bool:
    """判断该市场在 now_utc 时刻是否处于交易时段（按各自本地时间）。"""
    window = _MARKET_SESSION_WINDOWS.get(market)
    if not window:
        return True  # 未知市场沿用短阈值
    try:
        local_now = _to_local_display(now_utc, tz_name)
        return _session_bound(window[0], local_now) <= local_now <= _session_bound(window[1], local_now)
    except (ValueError, TypeError):
        return True


def _market_snapshot_stale(
    market: str,
    tz_name: str,
    quote_at_utc: datetime,
    now_utc: datetime,
    open_stale_after_seconds: float,
) -> bool:
    """开市时按采集间隔判定过期；闭市时只对长期无更新的快照判定过期。"""
    age_seconds = max(0.0, (now_utc - quote_at_utc).total_seconds())
    if _market_is_open(market, tz_name, now_utc):
        return age_seconds > open_stale_after_seconds
    return age_seconds > MARKET_CLOSED_STALE_SECONDS


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


def load_overview(config: dict, *, user_id: int | None = None) -> dict:
    init_db()
    owner_user_id = get_owner_user_id(config)
    interval = float(config.get("monitor", {}).get("interval_seconds", 30))
    stale_after = max(120.0, interval * 3)
    db = get_db()
    try:
        data_user_id = user_id if user_id is not None else owner_user_id
        if data_user_id is not None:
            portfolio_records = db.query(Portfolio).filter(
                Portfolio.user_id == data_user_id
            ).order_by(Portfolio.stock_code).all()
            watch_records = db.query(Watchlist).filter(
                Watchlist.user_id == data_user_id
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
        result = {
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
    result["a_share"] = load_a_share_overview(config)
    return result


def _market_snapshot_payload(
    snapshot: MarketQuoteSnapshot,
    stale_after_seconds: float,
    market: str,
    tz_name: str,
) -> dict:
    now = _utc_now()
    return {
        "price": snapshot.price,
        "change_pct": snapshot.change_pct,
        "currency": snapshot.currency,
        "quote_at": _to_local_display(snapshot.quote_at, "Asia/Shanghai"),
        "market_at": _to_local_display(snapshot.market_at, tz_name),
        "source": snapshot.source,
        "age_seconds": max(0.0, (now - snapshot.quote_at).total_seconds()),
        "stale": _market_snapshot_stale(
            market, tz_name, snapshot.quote_at, now, stale_after_seconds
        ),
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
            _market_snapshot_payload(
                snapshot, stale_after, definition.market, definition.timezone
            )
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
        "latest_quote_at": (
            _to_local_display(max(all_quote_times), "Asia/Shanghai")
            if all_quote_times
            else None
        ),
        "stale_after_seconds": stale_after,
        "source": "Yahoo Finance Chart API",
    }


def _sparkline_points(values: list[float], width: int = 180, height: int = 52) -> str:
    """Return safe numeric SVG points for a compact, dependency-free chart."""
    if not values:
        return ""
    if len(values) == 1:
        return f"0,{height / 2:.1f} {width},{height / 2:.1f}"
    low = min(values)
    high = max(values)
    span = high - low
    if span == 0:
        span = 1.0
    last_index = len(values) - 1
    points = []
    for index, value in enumerate(values):
        x = index / last_index * width
        y = height - ((value - low) / span * (height - 6) + 3)
        points.append(f"{x:.1f},{y:.1f}")
    return " ".join(points)


def _index_trend_label(momentum_20: float | None, above_ma20: bool | None) -> tuple[str, str]:
    if momentum_20 is None or above_ma20 is None:
        return "待确认", "pending"
    if momentum_20 >= 1.0 and above_ma20:
        return "偏强", "strong"
    if momentum_20 <= -1.0 and not above_ma20:
        return "偏弱", "weak"
    return "震荡", "flat"


def load_a_share_overview(config: dict) -> dict:
    """Load A-share index quotes and local trend context without network access."""

    definitions = [
        definition
        for definition in market_definitions(config, include_a_share=True)
        if definition.market == "a_share"
    ]
    enabled = global_markets_enabled(config)
    stale_after = max(900.0, market_poll_interval(config) * 3)
    empty_summary = {
        "label": "等待数据" if enabled else "未启用",
        "class": "pending",
        "description": (
            "等待指数采集器写入快照与本地日线。"
            if enabled
            else "跨市场采集已停用，无法生成指数趋势。"
        ),
        "above_ma20": 0,
        "available": 0,
        "average_momentum_20": None,
    }
    if not definitions:
        return {
            "enabled": enabled,
            "indices": [],
            "summary": empty_summary,
            "latest_quote_at": None,
            "as_of": None,
            "stale_after_seconds": stale_after,
            "source": "Yahoo Finance Chart API + 本地原始指数日线",
        }

    local_codes = [definition.local_code for definition in definitions if definition.local_code]
    init_db()
    db = get_db()
    try:
        snapshots = {
            (row.market, row.symbol): row
            for row in db.query(MarketQuoteSnapshot).filter(
                MarketQuoteSnapshot.market == "a_share"
            ).all()
        }
        bar_rows = db.query(DailyBar).filter(
            DailyBar.adjust == "",
            DailyBar.stock_code.in_(local_codes),
        ).order_by(DailyBar.stock_code, DailyBar.trade_date).all()
    finally:
        db.close()

    bars_by_code: dict[str, list[DailyBar]] = {}
    for row in bar_rows:
        bars_by_code.setdefault(row.stock_code, []).append(row)

    indices = []
    quote_times = []
    latest_bar_dates = []
    for definition in definitions:
        code_rows = bars_by_code.get(definition.local_code or "", [])
        window = code_rows[-60:]
        closes = [float(row.close) for row in window if row.close and row.close > 0]
        live_snapshot = snapshots.get((definition.market, definition.symbol))
        live_payload = (
            _market_snapshot_payload(
                live_snapshot, stale_after, definition.market, definition.timezone
            )
            if live_snapshot
            else None
        )
        if live_snapshot:
            quote_times.append(live_snapshot.quote_at)
        if code_rows:
            latest_bar_dates.append(code_rows[-1].trade_date)

        latest_close = closes[-1] if closes else None
        previous_close = closes[-2] if len(closes) >= 2 else None
        price = live_snapshot.price if live_snapshot else latest_close
        change_pct = (
            live_snapshot.change_pct
            if live_snapshot
            else ((latest_close / previous_close - 1) * 100
                  if latest_close and previous_close else None)
        )
        change_5 = (
            (latest_close / closes[-6] - 1) * 100
            if len(closes) >= 6 else None
        )
        change_20 = (
            (latest_close / closes[-21] - 1) * 100
            if len(closes) >= 21 else None
        )
        ma20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else None
        above_ma20 = price > ma20 if price is not None and ma20 else None
        trend_label, trend_class = _index_trend_label(change_20, above_ma20)
        if live_snapshot and not live_payload["stale"]:
            data_status = "实时"
        elif live_snapshot:
            data_status = "延迟"
        elif latest_close is not None:
            data_status = "收盘"
        else:
            data_status = "待采集"

        indices.append({
            "market": definition.market,
            "symbol": definition.symbol,
            "local_code": definition.local_code,
            "name": live_snapshot.name if live_snapshot else definition.name,
            "price": price,
            "change_pct": change_pct,
            "change_5": change_5,
            "change_20": change_20,
            "ma20": ma20,
            "above_ma20": above_ma20,
            "ma20_label": (
                "站上 MA20" if above_ma20 else "低于 MA20"
                if above_ma20 is not None else "MA20 样本不足"
            ),
            "trend_label": trend_label,
            "trend_class": trend_class,
            "data_status": data_status,
            "stale": bool(live_payload and live_payload["stale"]),
            "quote_at": live_snapshot.quote_at if live_snapshot else None,
            "market_at": live_snapshot.market_at if live_snapshot else None,
            "bar_date": code_rows[-1].trade_date if code_rows else None,
            "sparkline": _sparkline_points(closes[-30:]),
            "has_data": price is not None,
        })

    valid_trends = [
        row for row in indices
        if row["change_20"] is not None and row["above_ma20"] is not None
    ]
    if len(valid_trends) >= 3:
        average_momentum = sum(row["change_20"] for row in valid_trends) / len(valid_trends)
        above_count = sum(1 for row in valid_trends if row["above_ma20"])
        above_ratio = above_count / len(valid_trends)
        if above_ratio >= 0.75 and average_momentum >= 0:
            summary_label, summary_class = "偏强", "strong"
        elif above_ratio <= 0.25 and average_momentum <= 0:
            summary_label, summary_class = "偏弱", "weak"
        else:
            summary_label, summary_class = "震荡", "flat"
        description = (
            f"{above_count}/{len(valid_trends)} 个指数位于 20 日均线上方，"
            f"20 日平均变动 {average_momentum:+.1f}%"
        )
        if len(valid_trends) < len(indices):
            description += "；其余指数等待历史数据"
    else:
        average_momentum = None
        above_count = sum(1 for row in valid_trends if row["above_ma20"])
        summary_label, summary_class = "待确认", "pending"
        description = (
            f"当前仅有 {len(valid_trends)}/{len(indices)} 个指数具备完整趋势数据，"
            "至少需要 3 个指数的本地日线。"
        )

    return {
        "enabled": enabled,
        "indices": indices,
        "summary": {
            "label": summary_label,
            "class": summary_class,
            "description": description,
            "above_ma20": above_count,
            "available": len(valid_trends),
            "average_momentum_20": average_momentum,
        },
        "latest_quote_at": (
            _to_local_display(max(quote_times), "Asia/Shanghai")
            if quote_times
            else None
        ),
        "as_of": max(latest_bar_dates) if latest_bar_dates else None,
        "stale_after_seconds": stale_after,
        "source": "Yahoo Finance Chart API + 本地原始指数日线",
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


def _personal_codes(
    db,
    config: dict,
    user_id: int | None = None,
) -> tuple[set[str], set[str], dict[str, str]]:
    """Return portfolio/watchlist membership without making external requests."""
    owner_user_id = get_owner_user_id(config)
    names: dict[str, str] = {}
    data_user_id = user_id if user_id is not None else owner_user_id
    if data_user_id is not None:
        portfolios = db.query(Portfolio).filter(
            Portfolio.user_id == data_user_id
        ).all()
        watches = db.query(Watchlist).filter(
            Watchlist.user_id == data_user_id
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


def _compute_universe_scores(latest_date) -> dict:
    """Compute base candidate fields from the last 60 qfq bars per stock.

    Runs on a cache miss only; returns per-code dicts without personal
    portfolio/watchlist flags, which are overlaid per request.
    """

    init_db()
    db = get_db()
    try:
        inner = aliased(DailyBar)
        cutoff = (
            db.query(inner.trade_date)
            .filter(
                inner.adjust == "qfq",
                inner.stock_code == DailyBar.stock_code,
                inner.trade_date <= latest_date,
            )
            .order_by(inner.trade_date.desc())
            .limit(1)
            .offset(59)
            .scalar_subquery()
        )
        rows = db.query(DailyBar).filter(
            DailyBar.adjust == "qfq",
            DailyBar.stock_code.in_(RESEARCH_UNIVERSE),
            DailyBar.trade_date <= latest_date,
            DailyBar.trade_date >= cutoff,
        ).order_by(DailyBar.stock_code, DailyBar.trade_date).all()
    finally:
        db.close()

    latest_codes = {row.stock_code for row in rows if row.trade_date == latest_date}
    grouped: dict[str, list[DailyBar]] = {}
    for row in rows:
        if row.stock_code in latest_codes:
            grouped.setdefault(row.stock_code, []).append(row)

    computed: dict[str, dict] = {}
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
        }
        candidate["label"] = _candidate_label(score, momentum_20, candidate["above_ma20"])
        candidate["reasons"], candidate["objections"] = _candidate_explanation(candidate)
        computed[code] = candidate
    return computed


def _cached_universe_scores(latest_date) -> dict:
    """Return per-code base candidates, reusing the last aligned trading date."""
    now = time.monotonic()
    with _candidates_cache_lock:
        cached = _candidates_cache.get(latest_date)
        if cached is not None and now - cached[0] < CANDIDATES_CACHE_TTL_SECONDS:
            return cached[1]
    computed = _compute_universe_scores(latest_date)
    with _candidates_cache_lock:
        _candidates_cache.clear()  # 只保留最新一个交易日的结果，限制内存
        _candidates_cache[latest_date] = (time.monotonic(), computed)
    return computed


def _research_candidates(config: dict, user_id: int | None = None) -> dict:
    """Compute candidates from one aligned local qfq trading date.

    The expensive per-stock scoring is cached per trading date; personal
    portfolio/watchlist membership and names stay fresh on every request.
    """

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
        portfolio_codes, watchlist_codes, personal_names = _personal_codes(
            db, config, user_id
        )
        snapshot_names = {
            row.stock_code: row.name
            for row in db.query(QuoteSnapshot).filter(
                QuoteSnapshot.stock_code.in_(RESEARCH_UNIVERSE)
            ).all()
        }
    finally:
        db.close()

    candidates = []
    for code, base in _cached_universe_scores(latest_date).items():
        candidate = dict(base)
        candidate["name"] = (
            personal_names.get(code)
            or snapshot_names.get(code)
            or RESEARCH_UNIVERSE[code]
        )
        candidate["is_portfolio"] = code in portfolio_codes
        candidate["is_watchlist"] = code in watchlist_codes
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


def load_recommendations(
    config: dict,
    limit: int = 12,
    *,
    user_id: int | None = None,
) -> dict:
    """Return explainable, locally computed research candidates."""
    data = _research_candidates(config, user_id)
    data["candidates"] = data["candidates"][:limit]
    data["model"] = "V2 技术评分 + 趋势/风险过滤"
    return data


def load_screener(
    config: dict,
    *,
    user_id: int | None = None,
    min_score: int = 50,
    min_momentum: float = -100,
    max_volatility: float = 100,
    above_ma20: bool = False,
    limit: int = 20,
) -> dict:
    """Filter the aligned local research universe with reproducible criteria."""
    data = _research_candidates(config, user_id)
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

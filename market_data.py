"""Normalized local daily-bar repository and AKShare synchronization."""

from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime, timedelta
from typing import Optional

import akshare as ak
import pandas as pd
from sqlalchemy import func
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from database import DailyBar, get_db, init_db

logger = logging.getLogger(__name__)
_SINA_SLOTS = threading.BoundedSemaphore(value=2)

REQUIRED_AKSHARE_COLUMNS = {"日期", "开盘", "最高", "最低", "收盘", "成交量"}
ENGLISH_COLUMN_ALIASES = {
    "date": "日期",
    "open": "开盘",
    "high": "最高",
    "low": "最低",
    "close": "收盘",
    "volume": "成交量",
    "amount": "成交额",
}
NUMERIC_COLUMN_MAP = {
    "开盘": "open",
    "最高": "high",
    "最低": "low",
    "收盘": "close",
    "成交量": "volume",
    "成交额": "amount",
}


class MarketDataError(RuntimeError):
    """Raised when market data cannot be validated or synchronized."""


def normalize_stock_code(value: str) -> str:
    value = str(value).strip()
    if not value.isdigit() or not 1 <= len(value) <= 6:
        raise ValueError("股票代码必须是 1-6 位数字")
    return value.zfill(6)


def normalize_adjust(value: str) -> str:
    value = value.strip().lower()
    if value == "raw":
        value = ""
    if value not in {"", "qfq", "hfq"}:
        raise ValueError("复权方式必须是 raw、qfq 或 hfq")
    return value


def normalize_daily_bars(frame: pd.DataFrame, stock_code: str,
                         adjust: str = "qfq", source: str = "akshare") -> list[dict]:
    """Validate an AKShare daily frame and convert it to database records."""
    if frame is None or frame.empty:
        return []
    frame = frame.rename(columns={
        source_name: target_name
        for source_name, target_name in ENGLISH_COLUMN_ALIASES.items()
        if source_name in frame.columns and target_name not in frame.columns
    })
    missing = REQUIRED_AKSHARE_COLUMNS - set(frame.columns)
    if missing:
        raise MarketDataError(f"日线数据缺少字段: {', '.join(sorted(missing))}")

    code = normalize_stock_code(stock_code)
    adjust = normalize_adjust(adjust)
    normalized = pd.DataFrame()
    normalized["trade_date"] = pd.to_datetime(frame["日期"], errors="coerce").dt.date
    for source_column, target_column in NUMERIC_COLUMN_MAP.items():
        if source_column in frame.columns:
            normalized[target_column] = pd.to_numeric(frame[source_column], errors="coerce")
        elif target_column == "amount":
            normalized[target_column] = None

    required_numeric = ["open", "high", "low", "close", "volume"]
    normalized = normalized.dropna(subset=["trade_date", *required_numeric])
    normalized = normalized.drop_duplicates(subset=["trade_date"], keep="last")
    normalized = normalized.sort_values("trade_date")

    valid_prices = (
        (normalized["open"] > 0)
        & (normalized["high"] > 0)
        & (normalized["low"] > 0)
        & (normalized["close"] > 0)
        & (normalized["volume"] >= 0)
        & (normalized["high"] >= normalized[["open", "close", "low"]].max(axis=1))
        & (normalized["low"] <= normalized[["open", "close", "high"]].min(axis=1))
    )
    if not valid_prices.all():
        invalid_count = int((~valid_prices).sum())
        raise MarketDataError(f"发现 {invalid_count} 条价格或成交量不合法的日线")

    fetched_at = datetime.now()
    records = []
    for row in normalized.to_dict("records"):
        amount = row.get("amount")
        records.append({
            "stock_code": code,
            "trade_date": row["trade_date"],
            "adjust": adjust,
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row["volume"]),
            "amount": None if pd.isna(amount) else float(amount),
            "source": source,
            "fetched_at": fetched_at,
        })
    return records


def upsert_daily_bars(records: list[dict]) -> int:
    """Atomically insert or refresh normalized daily bars."""
    if not records:
        return 0
    init_db()
    db = get_db()
    try:
        statement = sqlite_insert(DailyBar).values(records)
        excluded = statement.excluded
        statement = statement.on_conflict_do_update(
            index_elements=["stock_code", "trade_date", "adjust"],
            set_={
                "open": excluded.open,
                "high": excluded.high,
                "low": excluded.low,
                "close": excluded.close,
                "volume": excluded.volume,
                "amount": excluded.amount,
                "source": excluded.source,
                "fetched_at": excluded.fetched_at,
            },
        )
        db.execute(statement)
        db.commit()
        return len(records)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def latest_cached_date(stock_code: str, adjust: str = "qfq") -> Optional[date]:
    code = normalize_stock_code(stock_code)
    adjust = normalize_adjust(adjust)
    init_db()
    db = get_db()
    try:
        return db.query(func.max(DailyBar.trade_date)).filter(
            DailyBar.stock_code == code,
            DailyBar.adjust == adjust,
        ).scalar()
    finally:
        db.close()


def load_daily_bars(stock_code: str, start_date: Optional[date] = None,
                    end_date: Optional[date] = None,
                    adjust: str = "qfq") -> pd.DataFrame:
    """Load normalized daily bars from SQLite in chronological order."""
    code = normalize_stock_code(stock_code)
    adjust = normalize_adjust(adjust)
    init_db()
    db = get_db()
    try:
        query = db.query(DailyBar).filter(
            DailyBar.stock_code == code,
            DailyBar.adjust == adjust,
        )
        if start_date is not None:
            query = query.filter(DailyBar.trade_date >= start_date)
        if end_date is not None:
            query = query.filter(DailyBar.trade_date <= end_date)
        rows = query.order_by(DailyBar.trade_date).all()
        return pd.DataFrame([
            {
                "stock_code": row.stock_code,
                "trade_date": row.trade_date,
                "open": row.open,
                "high": row.high,
                "low": row.low,
                "close": row.close,
                "volume": row.volume,
                "amount": row.amount,
                "adjust": row.adjust,
                "source": row.source,
            }
            for row in rows
        ])
    finally:
        db.close()


def _fetch_eastmoney(code: str, start_date: date, end_date: date,
                     adjust: str, attempts: int = 3) -> pd.DataFrame:
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            return ak.stock_zh_a_hist(
                symbol=code,
                period="daily",
                start_date=start_date.strftime("%Y%m%d"),
                end_date=end_date.strftime("%Y%m%d"),
                adjust=adjust,
                timeout=10,
            )
        except Exception as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(2 ** (attempt - 1))
    raise MarketDataError(f"东方财富数据源失败: {last_error}")


def _exchange_symbol(code: str) -> str:
    if code.startswith(("5", "6", "9")):
        return f"sh{code}"
    if code.startswith(("0", "1", "2", "3")):
        return f"sz{code}"
    raise MarketDataError(f"新浪备用源暂不支持该市场代码: {code}")


def _fetch_sina(code: str, start_date: date, end_date: date,
                adjust: str, timeout: int = 20) -> pd.DataFrame:
    if not _SINA_SLOTS.acquire(blocking=False):
        raise MarketDataError("新浪数据源工作线程已满")
    result = [None]
    error = [None]

    def target():
        try:
            result[0] = ak.stock_zh_a_daily(
                symbol=_exchange_symbol(code),
                start_date=start_date.strftime("%Y%m%d"),
                end_date=end_date.strftime("%Y%m%d"),
                adjust=adjust,
            )
        except Exception as exc:
            error[0] = exc
        finally:
            _SINA_SLOTS.release()

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise MarketDataError(f"新浪数据源超过 {timeout} 秒未响应")
    if error[0] is not None:
        raise MarketDataError(f"新浪数据源失败: {error[0]}")
    return result[0]


def _index_exchange_symbol(code: str) -> str:
    if code.startswith("000"):
        return f"sh{code}"
    if code.startswith("399"):
        return f"sz{code}"
    raise MarketDataError(f"指数源暂不支持该代码: {code}")


def _fetch_index(code: str, start_date: date, end_date: date,
                 timeout: int = 20) -> pd.DataFrame:
    if not _SINA_SLOTS.acquire(blocking=False):
        raise MarketDataError("新浪数据源工作线程已满")
    result = [None]
    error = [None]

    def target():
        try:
            result[0] = ak.stock_zh_index_daily(
                symbol=_index_exchange_symbol(code)
            )
        except Exception as exc:
            error[0] = exc
        finally:
            _SINA_SLOTS.release()

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise MarketDataError(f"新浪指数源超过 {timeout} 秒未响应")
    if error[0] is not None:
        raise MarketDataError(f"新浪指数源失败: {error[0]}")
    frame = result[0]
    if frame is None or frame.empty:
        return frame
    dates = pd.to_datetime(frame["date"], errors="coerce").dt.date
    return frame.loc[(dates >= start_date) & (dates <= end_date)].copy()


def _fetch_daily_bars(code: str, start_date: date, end_date: date,
                      adjust: str, source: str) -> tuple[pd.DataFrame, str]:
    if source not in {"auto", "eastmoney", "sina", "index"}:
        raise ValueError("source 必须是 auto、eastmoney、sina 或 index")
    if source == "index":
        if adjust:
            raise ValueError("指数日线只支持 raw，不支持复权")
        return _fetch_index(code, start_date, end_date), "sina_index"
    if source in {"auto", "eastmoney"}:
        try:
            return _fetch_eastmoney(code, start_date, end_date, adjust), "eastmoney"
        except MarketDataError:
            if source == "eastmoney":
                raise
            logger.warning(f"{code} 东方财富源失败，尝试新浪备用源")
    return _fetch_sina(code, start_date, end_date, adjust), "sina"


def sync_daily_bars(stock_code: str, start_date: Optional[date] = None,
                    end_date: Optional[date] = None, adjust: str = "qfq",
                    source: str = "auto") -> int:
    """Fetch and atomically synchronize daily bars for one stock."""
    code = normalize_stock_code(stock_code)
    adjust = normalize_adjust(adjust)
    if source == "index" and adjust:
        raise ValueError("指数日线请使用 --adjust raw")
    end_date = end_date or date.today()
    start_date = start_date or (end_date - timedelta(days=5 * 366))
    if start_date > end_date:
        raise ValueError("开始日期不能晚于结束日期")

    cached_end = latest_cached_date(code, adjust)
    effective_start = start_date
    # qfq 会在除权事件后重写历史价格，为保持一个区间内复权口径一致，
    # qfq 每次刷新请求区间；raw/hfq 可从最后缓存日重叠一天增量更新。
    if adjust != "qfq" and cached_end is not None:
        effective_start = max(start_date, cached_end)
    if effective_start > end_date:
        return 0

    try:
        frame, resolved_source = _fetch_daily_bars(
            code, effective_start, end_date, adjust, source
        )
    except MarketDataError as exc:
        raise MarketDataError(f"获取 {code} 日线失败: {exc}") from exc

    records = normalize_daily_bars(frame, code, adjust, source=resolved_source)
    if not records:
        logger.info(f"{code} 在请求区间内没有新增日线")
        return 0
    count = upsert_daily_bars(records)
    logger.info(
        f"{code} 日线同步完成: {records[0]['trade_date']} 至 "
        f"{records[-1]['trade_date']}，{count} 条"
    )
    return count

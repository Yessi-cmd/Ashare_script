"""Persist monitor output for read-only Web consumers."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from database import QuoteSnapshot, get_db, init_db

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def save_quote_snapshots(quotes: pd.DataFrame, score_details: dict) -> int:
    """Atomically upsert one monitoring cycle without external I/O."""
    if quotes is None or quotes.empty:
        return 0
    quote_at = datetime.now(SHANGHAI_TZ).replace(tzinfo=None)
    records = []
    for _, row in quotes.iterrows():
        code = str(row.get("代码", "")).zfill(6)
        price = pd.to_numeric(row.get("最新价"), errors="coerce")
        change_pct = pd.to_numeric(row.get("涨跌幅"), errors="coerce")
        if not code.isdigit() or pd.isna(price) or pd.isna(change_pct):
            continue
        score, reason = score_details.get(code, (None, None))
        volume = pd.to_numeric(row.get("成交量"), errors="coerce")
        records.append({
            "stock_code": code,
            "name": str(row.get("名称") or code),
            "price": float(price),
            "change_pct": float(change_pct),
            "volume": None if pd.isna(volume) else float(volume),
            "score": None if score is None else int(score),
            "reason": None if reason is None else str(reason)[:500],
            "quote_at": quote_at,
        })
    if not records:
        return 0

    init_db()
    db = get_db()
    try:
        statement = sqlite_insert(QuoteSnapshot).values(records)
        excluded = statement.excluded
        statement = statement.on_conflict_do_update(
            index_elements=["stock_code"],
            set_={
                "name": excluded.name,
                "price": excluded.price,
                "change_pct": excluded.change_pct,
                "volume": excluded.volume,
                "score": excluded.score,
                "reason": excluded.reason,
                "quote_at": excluded.quote_at,
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

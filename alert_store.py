"""Persistent alert cooldown storage."""

from database import AlertState, get_db


def load_alert_cache(owner_user_id: int) -> dict[str, float]:
    db = get_db()
    try:
        rows = db.query(AlertState).filter(AlertState.owner_user_id == owner_user_id).all()
        return {
            f"{row.stock_code}:{row.alert_type}": row.last_alerted_at
            for row in rows
        }
    finally:
        db.close()


def mark_alerted(owner_user_id: int, alerts: list, timestamp: float) -> None:
    db = get_db()
    try:
        for alert in alerts:
            row = db.query(AlertState).filter(
                AlertState.owner_user_id == owner_user_id,
                AlertState.stock_code == alert.stock_code,
                AlertState.alert_type == alert.alert_type,
            ).first()
            if row is None:
                row = AlertState(
                    owner_user_id=owner_user_id,
                    stock_code=alert.stock_code,
                    alert_type=alert.alert_type,
                    last_alerted_at=timestamp,
                )
                db.add(row)
            else:
                row.last_alerted_at = timestamp
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

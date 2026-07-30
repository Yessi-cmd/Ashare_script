#!/usr/bin/env python3
"""Create and prune verified online SQLite backups."""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

from database import DATABASE_URL


def sqlite_path_from_url(database_url: str) -> Path:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        raise ValueError("备份工具只支持 SQLite 数据库")
    return Path(database_url[len(prefix):]).resolve()


def create_backup(source: Path, destination_dir: Path, retention_days: int = 14) -> Path:
    source = source.resolve()
    destination_dir = destination_dir.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"数据库不存在: {source}")
    if retention_days < 1:
        raise ValueError("retention_days 必须大于 0")
    destination_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    target = destination_dir / f"ashare_monitor-{timestamp}.db"
    temporary = destination_dir / f".{target.name}.tmp"
    try:
        with sqlite3.connect(source) as source_db, sqlite3.connect(temporary) as backup_db:
            source_db.backup(backup_db)
            integrity = backup_db.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise RuntimeError(f"备份完整性检查失败: {integrity}")
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()

    cutoff = datetime.now() - timedelta(days=retention_days)
    for backup in destination_dir.glob("ashare_monitor-*.db"):
        if backup == target:
            continue
        if datetime.fromtimestamp(backup.stat().st_mtime) < cutoff:
            backup.unlink()
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="在线备份 A股监控 SQLite 数据库")
    parser.add_argument(
        "--source", type=Path, default=sqlite_path_from_url(DATABASE_URL)
    )
    parser.add_argument(
        "--destination",
        type=Path,
        default=Path(os.getenv("ASHARE_BACKUP_DIR", "/var/backups/ashare-monitor")),
    )
    parser.add_argument(
        "--retention-days",
        type=int,
        default=int(os.getenv("ASHARE_BACKUP_RETENTION_DAYS", "14")),
    )
    args = parser.parse_args()
    try:
        target = create_backup(args.source, args.destination, args.retention_days)
    except (OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
        print(f"❌ 备份失败: {exc}", file=sys.stderr)
        return 1
    print(f"✅ 备份完成: {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from backup_database import create_backup, sqlite_path_from_url


class BackupDatabaseTests(unittest.TestCase):
    def test_online_backup_is_valid_and_prunes_expired_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.db"
            destination = root / "backups"
            with sqlite3.connect(source) as db:
                db.execute("CREATE TABLE sample (value TEXT)")
                db.execute("INSERT INTO sample VALUES ('ok')")

            destination.mkdir()
            expired = destination / "ashare_monitor-20000101-000000.db"
            expired.touch()
            old = (datetime.now() - timedelta(days=30)).timestamp()
            os.utime(expired, (old, old))

            backup = create_backup(source, destination, retention_days=7)

            self.assertTrue(backup.exists())
            self.assertFalse(expired.exists())
            with sqlite3.connect(backup) as db:
                self.assertEqual(db.execute("SELECT value FROM sample").fetchone()[0], "ok")

    def test_database_url_must_be_sqlite(self):
        with self.assertRaisesRegex(ValueError, "只支持 SQLite"):
            sqlite_path_from_url("postgresql://localhost/test")


if __name__ == "__main__":
    unittest.main()

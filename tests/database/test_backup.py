"""
tests/database/test_backup.py

Confirms database/backup.py's Phase 1.2 migration: every time-dependent
function takes `now: datetime` explicitly instead of calling
datetime.now()/utcnow() itself. Uses FrozenClock throughout so backup
filename/date logic is deterministic to test.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from database import backup as backup_module

DAY_ONE = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
DAY_ONE_LATER = datetime(2026, 1, 1, 22, 0, 0, tzinfo=timezone.utc)
DAY_TWO = datetime(2026, 1, 2, 9, 0, 0, tzinfo=timezone.utc)


def _make_source_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (id TEXT)")
    conn.commit()
    conn.close()


class TestCreateBackup:
    def test_returns_none_if_source_does_not_exist(self, tmp_path: Path) -> None:
        result = backup_module.create_backup(
            tmp_path / "missing.db", tmp_path / "backups", reason="daily", now=DAY_ONE
        )
        assert result is None

    def test_creates_a_backup_file_named_with_the_given_time(self, tmp_path: Path) -> None:
        source = tmp_path / "source.db"
        _make_source_db(source)

        result = backup_module.create_backup(source, tmp_path / "backups", reason="daily", now=DAY_ONE)

        assert result is not None
        assert result.exists()
        assert "20260101_100000" in result.name
        assert "daily" in result.name

    def test_backup_is_a_valid_independent_database(self, tmp_path: Path) -> None:
        source = tmp_path / "source.db"
        _make_source_db(source)

        result = backup_module.create_backup(source, tmp_path / "backups", reason="daily", now=DAY_ONE)

        conn = sqlite3.connect(result)
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        conn.close()
        assert ("t",) in tables


class TestHasBackupToday:
    def test_false_when_no_backups_exist(self, tmp_path: Path) -> None:
        assert backup_module.has_backup_today(tmp_path / "backups", now=DAY_ONE) is False

    def test_true_after_a_backup_was_created_the_same_day(self, tmp_path: Path) -> None:
        source = tmp_path / "source.db"
        _make_source_db(source)
        backup_dir = tmp_path / "backups"
        backup_module.create_backup(source, backup_dir, reason="daily", now=DAY_ONE)

        assert backup_module.has_backup_today(backup_dir, now=DAY_ONE_LATER) is True

    def test_false_the_next_day(self, tmp_path: Path) -> None:
        source = tmp_path / "source.db"
        _make_source_db(source)
        backup_dir = tmp_path / "backups"
        backup_module.create_backup(source, backup_dir, reason="daily", now=DAY_ONE)

        assert backup_module.has_backup_today(backup_dir, now=DAY_TWO) is False


class TestEnsureDailyBackup:
    def test_creates_first_backup_of_the_day(self, tmp_path: Path) -> None:
        source = tmp_path / "source.db"
        _make_source_db(source)
        backup_dir = tmp_path / "backups"

        result = backup_module.ensure_daily_backup(source, backup_dir, now=DAY_ONE)
        assert result is not None

    def test_skips_a_second_backup_the_same_day(self, tmp_path: Path) -> None:
        source = tmp_path / "source.db"
        _make_source_db(source)
        backup_dir = tmp_path / "backups"

        first = backup_module.ensure_daily_backup(source, backup_dir, now=DAY_ONE)
        second = backup_module.ensure_daily_backup(source, backup_dir, now=DAY_ONE_LATER)

        assert first is not None
        assert second is None

    def test_creates_a_new_backup_the_next_day(self, tmp_path: Path) -> None:
        source = tmp_path / "source.db"
        _make_source_db(source)
        backup_dir = tmp_path / "backups"

        backup_module.ensure_daily_backup(source, backup_dir, now=DAY_ONE)
        second_day = backup_module.ensure_daily_backup(source, backup_dir, now=DAY_TWO)

        assert second_day is not None


class TestRotateBackups:
    def test_keeps_only_the_newest_n_backups(self, tmp_path: Path) -> None:
        source = tmp_path / "source.db"
        _make_source_db(source)
        backup_dir = tmp_path / "backups"

        for day_offset in range(5):
            backup_module.create_backup(
                source, backup_dir, reason="daily", now=DAY_ONE + timedelta(days=day_offset)
            )

        deleted = backup_module.rotate_backups(backup_dir, keep=2)
        remaining = sorted(backup_dir.glob("*.db"))

        assert len(deleted) == 3
        assert len(remaining) == 2
        # the two newest (by filename, which sorts chronologically) survive
        assert "20260106" in remaining[-1].name or "20260105" in remaining[-1].name

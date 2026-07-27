"""
database/backup.py

Backup mechanism for the SQLite database. Three responsibilities:

  1. create_backup()      -- creates a consistent backup (via SQLite's
                              online backup API, not a plain file copy
                              -- this is safe even during concurrent
                              writes / WAL mode).
  2. ensure_daily_backup() -- guarantees at most 1 automatic backup per
                              day (checks existing files by the date in
                              their name).
  3. rotate_backups()      -- a simple rotation policy: keeps the last
                              N backups, deletes older ones.

Backups are stored in data/backups/, outside the source tree (same as
the database itself) -- application updates never touch them.

Explicitly does NOT use shutil.copy directly on the .db file, since
under concurrent writes (even hypothetically, going forward) that could
copy an inconsistent state. sqlite3.Connection.backup() handles this
correctly.

Phase 1.2: no function in this module calls datetime.now()/utcnow()
directly anymore. The calling layer (database/database.py) supplies
the current time as an explicit `now: datetime` parameter, obtained
from an injected infrastructure.clock.Clock -- see infrastructure/README.md.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("ai_coach_keyholder.backup")

# Backup filename format: coach_keyholder_YYYYMMDD_HHMMSS_{reason}.db
# The date up front (after the prefix) makes both sorting and filtering
# for "today's backups" easy.
_BACKUP_GLOB = "*.db"


def _today_str(now: datetime) -> str:
    return now.strftime("%Y%m%d")


def _timestamp_str(now: datetime) -> str:
    return now.strftime("%Y%m%d_%H%M%S")


def create_backup(db_path: Path, backup_dir: Path, reason: str, now: datetime) -> Path | None:
    """
    Creates a database backup via SQLite's online backup API.

    Returns the path to the new backup, or None if the source database
    doesn't exist yet (typically the very first run -- nothing to back
    up).

    `now` must be timezone-aware UTC (same contract as
    infrastructure.clock.Clock.now()) -- used only for the filename,
    never validated or converted here (that's the Clock's
    responsibility).
    """
    if not db_path.exists():
        logger.info("Source database %s does not exist yet, skipping backup.", db_path)
        return None

    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_name = f"coach_keyholder_{_timestamp_str(now)}_{reason}.db"
    backup_path = backup_dir / backup_name

    source = sqlite3.connect(db_path)
    dest = sqlite3.connect(backup_path)
    try:
        source.backup(dest)
    finally:
        dest.close()
        source.close()

    logger.info("Created database backup: %s", backup_path)
    return backup_path


def has_backup_today(backup_dir: Path, now: datetime) -> bool:
    """Checks whether any automatic backup has already been made today (per `now`)."""
    if not backup_dir.exists():
        return False
    today = _today_str(now)
    for path in backup_dir.glob(_BACKUP_GLOB):
        # name: coach_keyholder_YYYYMMDD_HHMMSS_{reason}.db
        parts = path.stem.split("_")
        if len(parts) >= 3 and parts[2] == today:
            return True
    return False


def ensure_daily_backup(db_path: Path, backup_dir: Path, now: datetime) -> Path | None:
    """
    Creates an automatic backup with reason='daily' if none (of any
    kind -- both daily and pre_migration count) has been made today
    (per `now`) yet. Called on every application startup
    (bot/discord_bot.py main()).
    """
    if has_backup_today(backup_dir, now):
        logger.debug("Today's backup already exists, skipping.")
        return None
    return create_backup(db_path, backup_dir, reason="daily", now=now)


def rotate_backups(backup_dir: Path, keep: int) -> list[Path]:
    """
    Simple rotation policy: keeps the `keep` most recent backups by the
    timestamp in the filename, deletes older ones. Returns the list of
    deleted paths.

    No time dependency (sorts by filename, not by "now") -- no `now`
    parameter is needed.
    """
    if not backup_dir.exists():
        return []

    backups = sorted(backup_dir.glob(_BACKUP_GLOB), key=lambda p: p.name, reverse=True)
    to_delete = backups[keep:]

    deleted: list[Path] = []
    for path in to_delete:
        path.unlink()
        deleted.append(path)
        logger.info("Deleted old backup (rotation, keep=%d): %s", keep, path)

    return deleted

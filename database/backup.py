"""
database/backup.py

Zálohovací mechanismus pro SQLite databázi. Tři odpovědnosti:

  1. create_backup()      — vytvoří konzistentní zálohu (přes SQLite online
                             backup API, ne prosté kopírování souboru — to je
                             bezpečné i při souběžném zápisu / WAL módu).
  2. ensure_daily_backup() — zaručí max. 1 automatickou zálohu za den
                             (kontroluje existující soubory podle data v názvu).
  3. rotate_backups()      — jednoduchá rotační politika: ponechá posledních
                             N záloh, starší smaže.

Zálohy se ukládají do data/backups/, tedy mimo zdrojový kód (stejně jako
samotná databáze) — updaty programu se jich nedotknou.

Explicitně NEPOUŽÍVÁ shutil.copy na .db soubor přímo, protože při
souběžném zápisu (i teoretickém, do budoucna) by to mohlo zkopírovat
nekonzistentní stav. sqlite3.Connection.backup() řeší toto korektně.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("ai_coach_keyholder.backup")

# Formát názvu zálohy: coach_keyholder_YYYYMMDD_HHMMSS_{reason}.db
# Datum na začátku (po prefixu) usnadňuje řazení i filtrování "zálohy z dneška".
_BACKUP_GLOB = "*.db"


def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def _timestamp_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def create_backup(db_path: Path, backup_dir: Path, reason: str) -> Path | None:
    """
    Vytvoří zálohu databáze přes SQLite online backup API.

    Vrací cestu k nové záloze, nebo None pokud zdrojová databáze ještě
    neexistuje (typicky úplně první spuštění — není co zálohovat).
    """
    if not db_path.exists():
        logger.info("Zdrojová databáze %s zatím neexistuje, záloha se přeskakuje.", db_path)
        return None

    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_name = f"coach_keyholder_{_timestamp_str()}_{reason}.db"
    backup_path = backup_dir / backup_name

    source = sqlite3.connect(db_path)
    dest = sqlite3.connect(backup_path)
    try:
        source.backup(dest)
    finally:
        dest.close()
        source.close()

    logger.info("Vytvořena záloha databáze: %s", backup_path)
    return backup_path


def has_backup_today(backup_dir: Path) -> bool:
    """Zkontroluje, jestli už dnes vznikla jakákoli automatická záloha."""
    if not backup_dir.exists():
        return False
    today = _today_str()
    for path in backup_dir.glob(_BACKUP_GLOB):
        # název: coach_keyholder_YYYYMMDD_HHMMSS_{reason}.db
        parts = path.stem.split("_")
        if len(parts) >= 3 and parts[2] == today:
            return True
    return False


def ensure_daily_backup(db_path: Path, backup_dir: Path) -> Path | None:
    """
    Vytvoří automatickou zálohu s reason='daily', pokud dnes ještě žádná
    (jakéhokoli druhu — daily i pre_migration se počítají) nevznikla.
    Voláno při každém startu aplikace (bot/discord_bot.py main()).
    """
    if has_backup_today(backup_dir):
        logger.debug("Dnešní záloha už existuje, přeskakuji.")
        return None
    return create_backup(db_path, backup_dir, reason="daily")


def rotate_backups(backup_dir: Path, keep: int) -> list[Path]:
    """
    Jednoduchá rotační politika: ponechá `keep` nejnovějších záloh podle
    času v názvu souboru, starší smaže. Vrací seznam smazaných cest.
    """
    if not backup_dir.exists():
        return []

    backups = sorted(backup_dir.glob(_BACKUP_GLOB), key=lambda p: p.name, reverse=True)
    to_delete = backups[keep:]

    deleted: list[Path] = []
    for path in to_delete:
        path.unlink()
        deleted.append(path)
        logger.info("Smazána stará záloha (rotace, keep=%d): %s", keep, path)

    return deleted

"""
application/user_service.py

Maps a channel identity (e.g. a Discord user id) to an internal
UserAccount -- the application layer's own bookkeeping, independent of
every domain module (see application/models.py's UserAccount
docstring).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from application.models import UserAccount, new_id
from infrastructure.database import Database as CoreDatabase
from infrastructure.database import Transaction, apply_transition
from infrastructure.time_format import iso as _iso
from infrastructure.time_format import parse_iso as _parse_iso


class UserService:
    def __init__(self, db_path: str | Path, *, core: CoreDatabase | None = None) -> None:
        self.db_path = Path(db_path)
        self._core = core if core is not None else CoreDatabase(self.db_path)

    def get_or_create_user(self, channel: str, external_id: str, *, now: datetime) -> UserAccount:
        """
        Looks up an existing UserAccount by (channel, external_id); if
        found, updates last_seen_at and returns it. If not found,
        creates a new UserAccount + the identity mapping row, in one
        transaction. Never raises for "not found" -- creating on first
        contact is the whole point of this function.
        """
        def write(tx: Transaction, _state: object) -> UserAccount:
            identity_row = tx.fetch_one(
                "SELECT * FROM user_channel_identities WHERE channel = ? AND external_id = ?",
                (channel, external_id),
            )
            if identity_row is not None:
                account_row = tx.fetch_one(
                    "SELECT * FROM user_accounts WHERE id = ?", (identity_row["user_account_id"],),
                )
                tx.execute("UPDATE user_accounts SET last_seen_at = ? WHERE id = ?", (_iso(now), account_row["id"]))
                return UserAccount(id=account_row["id"], created_at=_parse_iso(account_row["created_at"]), last_seen_at=now)

            account = UserAccount(created_at=now, last_seen_at=now)
            tx.execute(
                "INSERT INTO user_accounts (id, created_at, last_seen_at) VALUES (?, ?, ?)",
                (account.id, _iso(now), _iso(now)),
            )
            tx.execute(
                "INSERT INTO user_channel_identities (id, user_account_id, channel, external_id, created_at) VALUES (?, ?, ?, ?, ?)",
                (new_id(), account.id, channel, external_id, _iso(now)),
            )
            return account

        return apply_transition(self._core, write=write)

    def get_user(self, user_account_id: str) -> UserAccount | None:
        with self._core.transaction() as tx:
            row = tx.fetch_one("SELECT * FROM user_accounts WHERE id = ?", (user_account_id,))
        if row is None:
            return None
        return UserAccount(id=row["id"], created_at=_parse_iso(row["created_at"]), last_seen_at=_parse_iso(row["last_seen_at"]))

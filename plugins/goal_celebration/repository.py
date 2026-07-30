"""
plugins/goal_celebration/repository.py

This plugin's own narrow repository -- receives `core` directly
(unlike PluginSDK, which never does; see
infrastructure/plugin_registry.py's own docstring, "Table ownership
and the trust boundary this implies," for exactly why that is a
deliberate, documented exception scoped to `owns_tables=True`
plugins' own repository.py, not a loophole). Owns exactly one table,
`goal_celebration_log`, and exposes only what this plugin's own
handlers actually need.
"""

from __future__ import annotations

from datetime import datetime

from infrastructure.database import Database as CoreDatabase, Transaction
from infrastructure.time_format import iso as _iso

__all__ = ["GoalCelebrationRepository", "build_repository"]


class GoalCelebrationRepository:
    """
    Stateless -- both of its methods take an already-open `tx` (this
    plugin's own handler is always called from inside
    `consume_event()`'s transaction, so there is never a legitimate
    reason for this repository to open its own). `build_repository()`
    still accepts `core` to match `PluginRegistry`'s own
    `build_repository(core)` convention, even though this particular
    repository has no use for it -- a plugin whose own operations
    genuinely need a fresh, self-opened transaction (e.g. a read that
    happens outside of any handler already inside one, such as from a
    future command) would store and use `core` here instead.
    """

    def has_been_celebrated_in_transaction(self, tx: Transaction, goal_group_id: str) -> bool:
        """Takes an already-open `tx` -- this plugin's own handler
        always calls this from inside `consume_event()`'s transaction,
        and a second, self-opened transaction here would raise
        `NestedTransactionError` the exact same way an earlier draft's
        `publish_event()` (not `publish_event_in_transaction()`) did
        when called from the same place -- a second real instance of
        the identical bug class, found and fixed the same way."""
        row = tx.fetch_one(
            "SELECT 1 FROM goal_celebration_log WHERE goal_group_id = ?", (goal_group_id,),
        )
        return row is not None

    def mark_celebrated_in_transaction(self, tx: Transaction, goal_group_id: str, *, now: datetime) -> None:
        """Takes an already-open `tx` -- called from within the event
        consumer handler's own transaction (`consume_event()`'s), never
        opens its own. Using `INSERT OR IGNORE` rather than a bare
        INSERT: if this ever raced with itself (e.g. a redelivery that
        somehow reached here twice within the same transaction), a
        duplicate PRIMARY KEY should be silently absorbed here, not
        raised as a surprising constraint violation -- the real,
        primary idempotency guarantee is still
        `has_been_celebrated_in_transaction()` being checked before
        this is ever called."""
        tx.execute(
            "INSERT OR IGNORE INTO goal_celebration_log (goal_group_id, celebrated_at) VALUES (?, ?)",
            (goal_group_id, _iso(now)),
        )


def build_repository(core: CoreDatabase) -> GoalCelebrationRepository:
    return GoalCelebrationRepository()

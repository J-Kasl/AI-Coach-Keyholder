"""
infrastructure/database.py

The shared, domain-agnostic transactional core every repository in this
system is built on. Owns:

  - SQLite connection creation and pragma configuration,
  - the single `transaction()` context manager that is the only way to
    open an atomic unit of work,
  - the `Transaction` object through which SQL is executed inside one,
  - `apply_transition()`, the generic load -> validate -> write ->
    events -> commit helper `implementation_conventions.md` Section 4
    describes.

Contains NO domain logic and NO knowledge of any specific table or
entity — see `database/database.py` for the coach_keyholder-specific
repository built on top of this (Fáze 1.2: that module now composes
this one instead of managing its own sqlite3 connections directly).
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Sequence, TypeVar

__all__ = [
    "Database",
    "Transaction",
    "NestedTransactionError",
    "apply_transition",
]

R = TypeVar("R")


class NestedTransactionError(RuntimeError):
    """
    Raised when `Database.transaction()` is called while a transaction
    is already open on the same `Database` instance.

    Nesting is deliberately forbidden, not given "explicit nesting
    semantics" via SAVEPOINT (which this wrapper does not expose): a
    domain operation that needs several logically distinct steps to be
    atomic together must be composed into ONE call to `transaction()`
    (or one `apply_transition()` call), not built by nesting two
    separate `transaction()` blocks. Nesting them would silently open a
    SECOND, independent SQLite connection to the same file while the
    first transaction is still open and uncommitted — at best
    surprising (the two connections' writes become visible to each
    other only after each independently commits, in whatever order that
    happens), at worst a lock-contention deadlock. Raising here trades
    a rare, easy-to-avoid restriction for never having to reason about
    that class of bug.
    """


class Transaction:
    """
    The only object through which SQL is executed inside an atomic
    operation. Deliberately does not expose `commit()`/`rollback()` —
    those belong exclusively to `Database.transaction()`, which is the
    one place a transaction's outcome is decided
    (`implementation_conventions.md` Section 4: a mutation and its
    event commit or roll back together, never partially, and never at
    the discretion of the code that merely wrote to a table).
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        return self._connection.execute(sql, params)

    def executemany(self, sql: str, seq_of_params: Iterable[Sequence[Any]]) -> sqlite3.Cursor:
        return self._connection.executemany(sql, seq_of_params)

    def fetch_one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        return self._connection.execute(sql, params).fetchone()

    def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        return self._connection.execute(sql, params).fetchall()


class Database:
    """
    Owns SQLite connection creation and the transactional boundary.
    Contains no domain-specific tables or methods.

    One `Database` instance is not safe for concurrent `transaction()`
    calls from multiple THREADS — the open-transaction guard is a plain
    instance attribute, not a lock. Use one instance per thread/task
    (this system's current scope — a single-process Discord bot — does
    not need cross-thread sharing of one instance; add explicit locking
    here if a future phase introduces one). This is distinct from, and
    does not weaken, this system's cross-PROCESS concurrency story:
    `BEGIN IMMEDIATE` (see `transaction()`) correctly serializes
    concurrent writers across separate OS processes at the SQLite
    engine level regardless of this class's own thread-safety scope.
    """

    def __init__(self, db_path: str | Path, *, busy_timeout_ms: int = 5000) -> None:
        if str(db_path) == ":memory:":
            raise ValueError(
                "Database does not support ':memory:'. It opens a new "
                "connection per transaction() call, and SQLite's ':memory:' "
                "database is unique PER CONNECTION unless shared-cache mode "
                "is enabled (which this wrapper does not do) — each "
                "transaction() call would see its own empty, separate "
                "database. Use a real temporary file instead, e.g. pytest's "
                "tmp_path fixture: Database(tmp_path / 'test.db')."
            )
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._busy_timeout_ms = busy_timeout_ms
        self._transaction_open = False

    def _connect(self) -> sqlite3.Connection:
        """
        One connection per transaction()/raw_connection() call — never
        pooled, never shared. WAL journal mode is set so readers never
        block a writer and vice versa, matching this system's
        single-writer/many-readers pattern. `busy_timeout` is set
        explicitly at the SQLite engine level (belt and suspenders
        alongside the Python driver's own `timeout=` retry loop, set to
        the same value) so lock contention waits and retries up to
        `busy_timeout_ms` before raising `sqlite3.OperationalError`,
        rather than failing on the very first contended attempt.
        """
        conn = sqlite3.connect(self.db_path, timeout=self._busy_timeout_ms / 1000)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
        return conn

    @contextmanager
    def transaction(self) -> Iterator[Transaction]:
        """
        The single, canonical way to open an atomic unit of work
        (`implementation_conventions.md` Section 4). Commits on normal
        exit, rolls back on any exception raised inside the `with`
        block, and always closes the connection.

        Uses `BEGIN IMMEDIATE`, not SQLite's default deferred `BEGIN` —
        matching this system's established convention
        (`implementation_conventions.md` Section 10) of acquiring the
        write lock at the start of a binding operation rather than
        discovering a write conflict partway through it.
        """
        if self._transaction_open:
            raise NestedTransactionError(
                "transaction() was called while a transaction is already "
                "open on this Database instance. Compose the two operations "
                "into a single transaction()/apply_transition() call instead "
                "of nesting two."
            )

        conn = self._connect()
        self._transaction_open = True
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield Transaction(conn)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._transaction_open = False
            conn.close()

    @contextmanager
    def raw_connection(self) -> Iterator[sqlite3.Connection]:
        """
        Escape hatch, deliberately separate from `transaction()`, for
        operations that must manage their own commit behavior —
        specifically, schema migrations applied via
        `sqlite3.Connection.executescript()`. `executescript()`
        implicitly commits any already-pending transaction before it
        runs, and does not participate in manual
        `BEGIN`/`COMMIT`/`ROLLBACK` control the same way `execute()`
        does — wrapping it in `transaction()`'s `BEGIN IMMEDIATE` would
        misrepresent the atomicity actually provided (a mid-script
        failure does not roll back statements already applied by that
        same `executescript()` call; this is a genuine SQLite/Python
        constraint, not a limitation of this wrapper).

        NOT intended for domain writes — use `transaction()` for those.
        NOT safe to use concurrently with an open `transaction()` against
        the SAME database file, even from within the same process/thread:
        each opens its own SQLite connection, and an open `transaction()`
        holds SQLite's write lock (`BEGIN IMMEDIATE`) until it exits —
        attempting `raw_connection()` while that lock is held raises
        `sqlite3.OperationalError` ("database is locked") once
        `busy_timeout_ms` elapses (confirmed by
        `tests/infrastructure/test_database.py::TestRawConnection`, not
        merely asserted here). This is not a defect to work around; it is
        what correctly using `raw_connection()` requires: only for
        migration-style work that runs BEFORE any `transaction()` is open
        (i.e. at startup, before the application begins normal
        operation), never nested inside one.

        Still commits on success and rolls back (whatever rollback means
        for a connection with no explicit `BEGIN` — effectively a no-op
        for anything `executescript()` already committed internally) on
        exception, and always closes the connection, for consistency with
        `transaction()`'s cleanup guarantees.
        """
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def apply_transition(
    db: Database,
    *,
    write: Callable[[Transaction, Any], R],
    load: Callable[[Transaction], Any] | None = None,
    validate: Callable[[Any], None] | None = None,
    events: Callable[[Transaction, Any, R], None] | None = None,
) -> R:
    """
    The generic load -> validate -> write -> events -> commit helper
    (`implementation_conventions.md` Section 4, the `_apply_transition`
    pattern). Domain-agnostic — knows nothing about any specific table.

    Only `write` is required. Most of today's repository methods (a
    single INSERT with no precondition to check and, as yet, no event
    to append) need nothing else — `write` receives `(tx, None)` and
    can ignore the second argument. `load`/`validate` exist for an
    operation that must read and check current state before writing
    (`write` then receives `(tx, state)`, `state` being whatever `load`
    returned).

    `events` is the deliberately-already-present slot for the future
    transactional outbox: unused today, since `domain_events` does not
    exist yet (out of this phase's scope), but its signature is fixed
    now so that adding real event-writing logic in the outbox phase
    means passing an `events=` callable at whichever call sites need
    one — never changing this function's signature, and never touching
    any existing call site that doesn't pass one.

    All callables run inside the SAME transaction — a failure at any
    step rolls back everything already done in this call, including
    anything `write` already executed before the failure.
    """
    with db.transaction() as tx:
        state = load(tx) if load is not None else None
        if validate is not None:
            validate(state)
        result = write(tx, state)
        if events is not None:
            events(tx, state, result)
        return result

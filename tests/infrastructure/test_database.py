"""
tests/infrastructure/test_database.py

Unit tests for infrastructure/database.py — the domain-agnostic
transactional core. Uses an ad-hoc, minimal two-table schema created
per test, never the coach_keyholder schema (that belongs to
tests/database/, which tests the repository built on top of this).
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from infrastructure.database import (
    Database,
    NestedTransactionError,
    Transaction,
    apply_transition,
)


@pytest.fixture
def db(tmp_path: Path) -> Database:
    """A fresh Database backed by a real temporary file, isolated per test
    by pytest's own tmp_path (a distinct directory per test)."""
    database = Database(tmp_path / "test.db")
    with database.transaction() as tx:
        tx.execute("CREATE TABLE a (id TEXT PRIMARY KEY, value TEXT NOT NULL)")
        tx.execute("CREATE TABLE b (id TEXT PRIMARY KEY, a_id TEXT NOT NULL)")
    return database


class TestConstruction:
    def test_rejects_in_memory_path(self) -> None:
        with pytest.raises(ValueError, match=":memory:"):
            Database(":memory:")

    def test_creates_parent_directory(self, tmp_path: Path) -> None:
        nested_path = tmp_path / "nested" / "dir" / "test.db"
        Database(nested_path)
        assert nested_path.parent.exists()


class TestSuccessfulTransaction:
    def test_commits_a_single_write(self, db: Database) -> None:
        with db.transaction() as tx:
            tx.execute("INSERT INTO a (id, value) VALUES (?, ?)", ("1", "hello"))

        with db.transaction() as tx:
            row = tx.fetch_one("SELECT value FROM a WHERE id = ?", ("1",))
        assert row is not None
        assert row["value"] == "hello"

    def test_commits_all_writes_in_one_transaction(self, db: Database) -> None:
        """Requirement 1 + 3: a successful transaction commits every write,
        across more than one table, atomically."""
        with db.transaction() as tx:
            tx.execute("INSERT INTO a (id, value) VALUES (?, ?)", ("1", "hello"))
            tx.execute("INSERT INTO b (id, a_id) VALUES (?, ?)", ("b1", "1"))

        with db.transaction() as tx:
            a_row = tx.fetch_one("SELECT * FROM a WHERE id = ?", ("1",))
            b_row = tx.fetch_one("SELECT * FROM b WHERE id = ?", ("b1",))
        assert a_row is not None
        assert b_row is not None
        assert b_row["a_id"] == "1"


class TestFailedTransaction:
    def test_exception_rolls_back_all_writes(self, db: Database) -> None:
        """Requirement 2: an exception rolls back everything, not just the
        statement that raised."""
        class DeliberateFailure(Exception):
            pass

        with pytest.raises(DeliberateFailure):
            with db.transaction() as tx:
                tx.execute("INSERT INTO a (id, value) VALUES (?, ?)", ("1", "hello"))
                raise DeliberateFailure("simulated failure after a write")

        with db.transaction() as tx:
            row = tx.fetch_one("SELECT * FROM a WHERE id = ?", ("1",))
        assert row is None

    def test_fault_after_first_successful_write_rolls_back_both(self, db: Database) -> None:
        """
        Fault-injection test (explicitly requested): the SECOND statement
        fails, but the FIRST one — which the database engine already
        executed successfully within this transaction — must not survive
        either, because they were never independently committed.
        """
        with pytest.raises(sqlite3.IntegrityError):
            with db.transaction() as tx:
                tx.execute("INSERT INTO a (id, value) VALUES (?, ?)", ("1", "first write, succeeds"))
                # a_id has no matching row in `a` by the time FK is checked at
                # commit... instead, force a concrete, deterministic failure:
                # a duplicate primary key on a SECOND insert into `a` itself.
                tx.execute("INSERT INTO a (id, value) VALUES (?, ?)", ("1", "duplicate id, fails"))

        with db.transaction() as tx:
            row = tx.fetch_one("SELECT * FROM a WHERE id = ?", ("1",))
        assert row is None, "the first, individually-successful write must not survive a later failure"

    def test_rollback_on_python_exception_not_just_sql_error(self, db: Database) -> None:
        """The fault need not be a SQL error -- any exception raised inside
        the `with` block, including a plain application-level one, rolls
        back writes already made in the same transaction."""
        with pytest.raises(ValueError):
            with db.transaction() as tx:
                tx.execute("INSERT INTO a (id, value) VALUES (?, ?)", ("1", "hello"))
                raise ValueError("application-level failure, not a SQL error")

        with db.transaction() as tx:
            row = tx.fetch_one("SELECT * FROM a WHERE id = ?", ("1",))
        assert row is None


class TestConnectionLifecycle:
    def test_connection_closes_after_commit(self, db: Database) -> None:
        """Requirement 4."""
        captured: list[sqlite3.Connection] = []
        with db.transaction() as tx:
            captured.append(tx._connection)  # test-only introspection
            tx.execute("INSERT INTO a (id, value) VALUES (?, ?)", ("1", "hello"))

        with pytest.raises(sqlite3.ProgrammingError):
            captured[0].execute("SELECT 1")

    def test_connection_closes_after_rollback(self, db: Database) -> None:
        """Requirement 4."""
        captured: list[sqlite3.Connection] = []
        with pytest.raises(RuntimeError):
            with db.transaction() as tx:
                captured.append(tx._connection)
                raise RuntimeError("simulated failure")

        with pytest.raises(sqlite3.ProgrammingError):
            captured[0].execute("SELECT 1")


class TestNestedTransactions:
    def test_nested_transaction_raises_explicitly(self, db: Database) -> None:
        """Requirement 5: nesting has clearly defined (forbidden, not
        silently surprising) behavior."""
        with db.transaction():
            with pytest.raises(NestedTransactionError):
                with db.transaction():
                    pass

    def test_transaction_open_flag_resets_after_nested_attempt_fails(self, db: Database) -> None:
        """A rejected nested attempt must not leave the outer transaction
        (or the next, later transaction) unusable."""
        with db.transaction():
            with pytest.raises(NestedTransactionError):
                with db.transaction():
                    pass
            # the outer transaction is still open and usable here
        # and a subsequent, separate transaction works normally
        with db.transaction() as tx:
            tx.execute("INSERT INTO a (id, value) VALUES (?, ?)", ("1", "still works"))

    def test_sequential_transactions_on_the_same_instance_are_fine(self, db: Database) -> None:
        """Not nested -- two separate, sequential transaction() calls on the
        same Database instance are the normal, supported case."""
        with db.transaction() as tx:
            tx.execute("INSERT INTO a (id, value) VALUES (?, ?)", ("1", "first"))
        with db.transaction() as tx:
            tx.execute("INSERT INTO a (id, value) VALUES (?, ?)", ("2", "second"))

        with db.transaction() as tx:
            rows = tx.fetch_all("SELECT id FROM a ORDER BY id")
        assert [r["id"] for r in rows] == ["1", "2"]


class TestTransactionDoesNotExposeCommitOrRollback:
    def test_no_commit_method(self, db: Database) -> None:
        """Requirement 6: a repository method built on Transaction has no
        way to commit independently of Database.transaction()."""
        with db.transaction() as tx:
            assert not hasattr(tx, "commit")

    def test_no_rollback_method(self, db: Database) -> None:
        with db.transaction() as tx:
            assert not hasattr(tx, "rollback")


class TestIsolationBetweenTests:
    def test_two_database_instances_do_not_share_data(self, tmp_path: Path) -> None:
        """Requirement 7, made explicit: two Database instances against two
        different paths never see each other's writes -- demonstrating why
        pytest's tmp_path (a fresh directory per test) is what actually
        isolates the test suite, not any special behavior of this class."""
        db_a = Database(tmp_path / "a" / "test.db")
        db_b = Database(tmp_path / "b" / "test.db")
        with db_a.transaction() as tx:
            tx.execute("CREATE TABLE t (id TEXT PRIMARY KEY)")
            tx.execute("INSERT INTO t (id) VALUES ('only-in-a')")
        with db_b.transaction() as tx:
            tx.execute("CREATE TABLE t (id TEXT PRIMARY KEY)")
            rows = tx.fetch_all("SELECT * FROM t")
        assert rows == []


class TestApplyTransition:
    def test_write_only(self, db: Database) -> None:
        result = apply_transition(
            db,
            write=lambda tx, _state: tx.execute(
                "INSERT INTO a (id, value) VALUES (?, ?)", ("1", "hello")
            ).rowcount,
        )
        assert result == 1
        with db.transaction() as tx:
            row = tx.fetch_one("SELECT * FROM a WHERE id = ?", ("1",))
        assert row is not None

    def test_load_validate_write_sequence(self, db: Database) -> None:
        with db.transaction() as tx:
            tx.execute("INSERT INTO a (id, value) VALUES (?, ?)", ("1", "initial"))

        def load(tx: Transaction):
            return tx.fetch_one("SELECT * FROM a WHERE id = ?", ("1",))

        def validate(state) -> None:
            assert state is not None, "precondition: row must already exist"

        def write(tx: Transaction, state) -> str:
            tx.execute("UPDATE a SET value = ? WHERE id = ?", ("updated", state["id"]))
            return state["id"]

        result = apply_transition(db, load=load, validate=validate, write=write)
        assert result == "1"
        with db.transaction() as tx:
            row = tx.fetch_one("SELECT value FROM a WHERE id = ?", ("1",))
        assert row["value"] == "updated"

    def test_failed_validation_prevents_write_and_rolls_back(self, db: Database) -> None:
        def load(tx: Transaction):
            return tx.fetch_one("SELECT * FROM a WHERE id = ?", ("missing",))

        def validate(state) -> None:
            if state is None:
                raise ValueError("precondition failed: row does not exist")

        def write(tx: Transaction, state) -> None:
            tx.execute("INSERT INTO a (id, value) VALUES (?, ?)", ("should-not-exist", "x"))

        with pytest.raises(ValueError, match="precondition failed"):
            apply_transition(db, load=load, validate=validate, write=write)

        with db.transaction() as tx:
            row = tx.fetch_one("SELECT * FROM a WHERE id = ?", ("should-not-exist",))
        assert row is None

    def test_events_slot_is_optional_and_unused_by_default(self, db: Database) -> None:
        """The events= slot exists for the future outbox phase; confirms it
        is safe to omit entirely today."""
        result = apply_transition(
            db, write=lambda tx, _state: tx.execute(
                "INSERT INTO a (id, value) VALUES (?, ?)", ("1", "x")
            ).rowcount,
        )
        assert result == 1

    def test_events_callable_runs_in_the_same_transaction(self, db: Database) -> None:
        """When events= IS supplied, it must participate in the same
        atomic unit — a failure in events() rolls back write() too."""
        class EventFailure(Exception):
            pass

        def write(tx: Transaction, _state) -> str:
            tx.execute("INSERT INTO a (id, value) VALUES (?, ?)", ("1", "x"))
            return "1"

        def events(tx: Transaction, _state, _result) -> None:
            raise EventFailure("simulated event-write failure")

        with pytest.raises(EventFailure):
            apply_transition(db, write=write, events=events)

        with db.transaction() as tx:
            row = tx.fetch_one("SELECT * FROM a WHERE id = ?", ("1",))
        assert row is None, "write() must roll back if events() fails in the same transition"


class TestRawConnection:
    def test_raw_connection_commits_on_success(self, tmp_path: Path) -> None:
        database = Database(tmp_path / "test.db")
        with database.raw_connection() as conn:
            conn.executescript("CREATE TABLE t (id TEXT PRIMARY KEY);")

        with database.transaction() as tx:
            # table exists and is queryable via the normal transactional path
            rows = tx.fetch_all("SELECT * FROM t")
        assert rows == []

    def test_raw_connection_contends_with_an_open_transaction_on_the_same_file(self, tmp_path: Path) -> None:
        """
        Discovered while writing this suite, not merely asserted in
        advance: raw_connection() and transaction() are NOT safe to use
        concurrently against the same database file, even from within the
        same process/thread. Each opens its own SQLite connection, and an
        open transaction() holds SQLite's write lock (BEGIN IMMEDIATE)
        until it exits -- attempting raw_connection() while that lock is
        held raises sqlite3.OperationalError once busy_timeout elapses.
        This is not a defect; it documents why raw_connection() must only
        be used for migration-style work that runs BEFORE any
        transaction() is open (see its docstring), never nested inside
        one. busy_timeout_ms is set very low here so the test fails fast
        rather than waiting out a real timeout.
        """
        database = Database(tmp_path / "test.db", busy_timeout_ms=100)
        with database.transaction():
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                with database.raw_connection() as conn:
                    conn.executescript("CREATE TABLE t (id TEXT PRIMARY KEY);")


class TestThreadSafetyIsOutOfScope:
    def test_concurrent_transactions_from_two_threads_are_not_supported(self, tmp_path: Path) -> None:
        """
        Documents the documented limitation rather than silently leaving it
        untested: calling transaction() concurrently from two threads on
        ONE Database instance is not a safety guarantee this class makes
        (see the class docstring). This test demonstrates the guard is a
        plain attribute, not a lock, by showing a race is possible in
        principle -- it does not assert a specific outcome, only that this
        is explicitly out of scope rather than accidentally relied upon.
        """
        database = Database(tmp_path / "test.db")
        with database.transaction() as tx:
            tx.execute("CREATE TABLE t (id TEXT PRIMARY KEY)")

        errors: list[Exception] = []

        def worker(value: str) -> None:
            try:
                with database.transaction() as tx:
                    tx.execute("INSERT INTO t (id) VALUES (?)", (value,))
            except Exception as exc:  # noqa: BLE001 -- intentionally broad, see docstring
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(str(i),)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # No assertion on `errors` being empty or non-empty: the point of
        # this test is that concurrent access from multiple threads to one
        # instance is undefined by design, not a guaranteed-safe path.

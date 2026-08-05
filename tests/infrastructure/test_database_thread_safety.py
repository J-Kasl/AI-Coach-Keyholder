"""tests/infrastructure/test_database_thread_safety.py"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from infrastructure.database import Database, NestedTransactionError

FIXED_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "test.db")


def _bootstrap_table(db: Database) -> None:
    with db.raw_connection() as conn:
        conn.executescript("CREATE TABLE t (id INTEGER PRIMARY KEY, v INTEGER)")


class TestNestedTransactionSameThread:
    def test_nesting_in_the_same_thread_still_raises(self, db: Database) -> None:
        with db.transaction():
            with pytest.raises(NestedTransactionError):
                with db.transaction():
                    pass


class TestConcurrentThreadsSameInstance:
    def test_two_threads_open_transactions_concurrently_without_error(self, db: Database) -> None:
        _bootstrap_table(db)
        errors: list[Exception] = []
        barrier = threading.Barrier(2)

        def work() -> None:
            try:
                barrier.wait(timeout=5)
                with db.transaction() as tx:
                    tx.execute("INSERT INTO t (v) VALUES (?)", (1,))
            except Exception as exc:  # pragma: no cover -- failure path only
                errors.append(exc)

        threads = [threading.Thread(target=work) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert errors == []
        with db.transaction() as tx:
            count = tx.fetch_one("SELECT COUNT(*) as n FROM t")["n"]
        assert count == 2

    def test_concurrent_writes_do_not_lose_data(self, db: Database) -> None:
        _bootstrap_table(db)
        errors: list[Exception] = []

        def work(n: int) -> None:
            try:
                with db.transaction() as tx:
                    tx.execute("INSERT INTO t (v) VALUES (?)", (n,))
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=work, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert errors == []
        with db.transaction() as tx:
            count = tx.fetch_one("SELECT COUNT(*) as n FROM t")["n"]
        assert count == 20

    def test_exception_in_one_thread_resets_its_own_thread_local_state(self, db: Database) -> None:
        _bootstrap_table(db)

        def failing_work() -> None:
            with pytest.raises(RuntimeError):
                with db.transaction():
                    raise RuntimeError("boom")

        t = threading.Thread(target=failing_work)
        t.start()
        t.join(timeout=5)

        # The SAME thread that failed should be able to open a fresh
        # transaction immediately -- proves the guard reset in finally.
        def reuse() -> None:
            with db.transaction() as tx:
                tx.execute("INSERT INTO t (v) VALUES (?)", (99,))

        t2 = threading.Thread(target=reuse)
        t2.start()
        t2.join(timeout=5)

        with db.transaction() as tx:
            count = tx.fetch_one("SELECT COUNT(*) as n FROM t")["n"]
        assert count == 1

    def test_worker_thread_can_be_reused_after_completed_transaction(self, db: Database) -> None:
        """Relevant to asyncio.to_thread()'s own ThreadPoolExecutor
        reusing worker threads across calls."""
        _bootstrap_table(db)

        def work() -> None:
            with db.transaction() as tx:
                tx.execute("INSERT INTO t (v) VALUES (?)", (1,))
            with db.transaction() as tx:  # second, sequential use of the SAME thread
                tx.execute("INSERT INTO t (v) VALUES (?)", (2,))

        t = threading.Thread(target=work)
        t.start()
        t.join(timeout=5)

        with db.transaction() as tx:
            count = tx.fetch_one("SELECT COUNT(*) as n FROM t")["n"]
        assert count == 2

    def test_one_threads_state_does_not_affect_another(self, db: Database) -> None:
        results: dict[str, bool] = {}
        ready = threading.Event()
        release = threading.Event()

        def hold_open() -> None:
            with db.transaction():
                results["a_open"] = db._is_transaction_open_in_current_thread()
                ready.set()
                release.wait(timeout=5)

        def check_other() -> None:
            results["b_sees_own_state_false"] = not db._is_transaction_open_in_current_thread()

        t1 = threading.Thread(target=hold_open)
        t1.start()
        ready.wait(timeout=5)

        t2 = threading.Thread(target=check_other)
        t2.start()
        t2.join(timeout=5)

        release.set()
        t1.join(timeout=5)

        assert results["a_open"] is True
        assert results["b_sees_own_state_false"] is True

    def test_original_twenty_thread_reproduction_no_longer_produces_nested_transaction_error(
        self, tmp_path: Path,
    ) -> None:
        """The exact scenario from the review's own empirical experiment."""
        from application.models import IncomingMessage
        from application.service import ApplicationService

        core = Database(tmp_path / "app.db")
        migrations_dir = Path(__file__).parent.parent.parent / "database" / "migrations"
        with core.raw_connection() as conn:
            for path in sorted(migrations_dir.glob("*.sql")):
                conn.executescript(path.read_text(encoding="utf-8"))

        service = ApplicationService(core.db_path, core=core)
        errors: list[tuple[str, str]] = []

        def onboard(user_id: str) -> None:
            try:
                for text in ("anything", "english", "neutral", "alex", "status"):
                    service.handle_message(
                        IncomingMessage(channel="discord", external_user_id=user_id, text=text, received_at=FIXED_TIME),
                    )
            except Exception as exc:  # pragma: no cover
                errors.append((user_id, type(exc).__name__))

        threads = [threading.Thread(target=onboard, args=(str(i),)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        assert errors == []


class TestPublicAPIUnchanged:
    def test_transaction_is_still_a_context_manager_returning_transaction(self, db: Database) -> None:
        from infrastructure.database import Transaction

        with db.transaction() as tx:
            assert isinstance(tx, Transaction)

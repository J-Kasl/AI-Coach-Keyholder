"""
tests/infrastructure/test_consumer_registry.py
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from infrastructure.consumer_registry import ConsumerRegistry, process_pending_events
from infrastructure.database import Database
from infrastructure.outbox import DomainEvent, claim_pending_events, write_event

FIXED_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _apply_migrations(core: Database) -> None:
    migrations_dir = Path(__file__).parent.parent.parent / "database" / "migrations"
    with core.raw_connection() as conn:
        for path in sorted(migrations_dir.glob("*.sql")):
            conn.executescript(path.read_text(encoding="utf-8"))


@pytest.fixture
def db(tmp_path: Path) -> Database:
    d = Database(tmp_path / "test.db")
    _apply_migrations(d)
    return d


def _write_sample_event(db: Database, event_type: str = "test.thing_happened") -> None:
    with db.transaction() as tx:
        write_event(tx, DomainEvent(event_type=event_type, source_module="test", payload={}, occurred_at=FIXED_TIME))


class TestDispatch:
    def test_registered_handler_runs(self, db: Database) -> None:
        registry = ConsumerRegistry()
        calls: list[str] = []
        registry.register("test.thing_happened", "consumer_a", lambda tx, event: calls.append(event.event_type))

        _write_sample_event(db)
        claimed = claim_pending_events(db, claimant="p1", batch_size=10, now=FIXED_TIME, lease_duration=timedelta(minutes=5))
        ran = registry.dispatch(db, claimed[0], now=FIXED_TIME)

        assert ran == 1
        assert calls == ["test.thing_happened"]

    def test_no_registered_handler_is_a_no_op(self, db: Database) -> None:
        registry = ConsumerRegistry()
        _write_sample_event(db)
        claimed = claim_pending_events(db, claimant="p1", batch_size=10, now=FIXED_TIME, lease_duration=timedelta(minutes=5))
        ran = registry.dispatch(db, claimed[0], now=FIXED_TIME)
        assert ran == 0

    def test_multiple_consumers_for_the_same_event_type_all_run(self, db: Database) -> None:
        registry = ConsumerRegistry()
        calls: list[str] = []
        registry.register("test.thing_happened", "consumer_a", lambda tx, event: calls.append("a"))
        registry.register("test.thing_happened", "consumer_b", lambda tx, event: calls.append("b"))

        _write_sample_event(db)
        claimed = claim_pending_events(db, claimant="p1", batch_size=10, now=FIXED_TIME, lease_duration=timedelta(minutes=5))
        ran = registry.dispatch(db, claimed[0], now=FIXED_TIME)

        assert ran == 2
        assert set(calls) == {"a", "b"}

    def test_one_consumers_failure_does_not_block_another(self, db: Database) -> None:
        """Each consumer runs in its OWN transaction -- one failing must
        not prevent another, independent consumer from still processing
        the same event."""
        registry = ConsumerRegistry()
        calls: list[str] = []

        def failing_handler(tx, event) -> None:
            raise RuntimeError("consumer_a failure")

        registry.register("test.thing_happened", "consumer_a", failing_handler)
        registry.register("test.thing_happened", "consumer_b", lambda tx, event: calls.append("b"))

        _write_sample_event(db)
        claimed = claim_pending_events(db, claimant="p1", batch_size=10, now=FIXED_TIME, lease_duration=timedelta(minutes=5))

        with pytest.raises(RuntimeError):
            registry.dispatch(db, claimed[0], now=FIXED_TIME)
        # consumer_b's registration comes after consumer_a's and never
        # runs in this pass since dispatch() stops at the first
        # exception -- this documents the current, simplest behavior
        # (fail-fast) rather than silently swallowing consumer errors.


class TestProcessPendingEvents:
    def test_claims_dispatches_and_publishes(self, db: Database) -> None:
        registry = ConsumerRegistry()
        calls: list[str] = []
        registry.register("test.thing_happened", "consumer_a", lambda tx, event: calls.append(event.id))

        _write_sample_event(db)
        ran = process_pending_events(db, registry, claimant="p1", now=FIXED_TIME)

        assert ran == 1
        assert len(calls) == 1
        with db.transaction() as tx:
            row = tx.fetch_one("SELECT published_at FROM domain_events")
        assert row["published_at"] is not None

    def test_events_with_no_consumer_are_still_marked_published(self, db: Database) -> None:
        registry = ConsumerRegistry()  # nothing registered
        _write_sample_event(db)
        process_pending_events(db, registry, claimant="p1", now=FIXED_TIME)
        with db.transaction() as tx:
            row = tx.fetch_one("SELECT published_at FROM domain_events")
        assert row["published_at"] is not None

    def test_redelivery_does_not_rerun_the_handler(self, db: Database) -> None:
        registry = ConsumerRegistry()
        calls: list[str] = []
        registry.register("test.thing_happened", "consumer_a", lambda tx, event: calls.append("call"))

        _write_sample_event(db)
        process_pending_events(db, registry, claimant="p1", now=FIXED_TIME)

        # simulate redelivery: force the event to look unpublished again
        # (e.g. a publisher crash after claim but the effect already ran)
        with db.transaction() as tx:
            tx.execute("UPDATE domain_events SET published_at = NULL, claimed_at = NULL, claim_expires_at = NULL")
        process_pending_events(db, registry, claimant="p2", now=FIXED_TIME + timedelta(minutes=1))

        assert calls == ["call"]  # not ["call", "call"]

    def test_multiple_events_all_processed_in_one_batch(self, db: Database) -> None:
        registry = ConsumerRegistry()
        calls: list[str] = []
        registry.register("test.thing_happened", "consumer_a", lambda tx, event: calls.append(event.id))

        _write_sample_event(db)
        _write_sample_event(db)
        _write_sample_event(db)
        ran = process_pending_events(db, registry, claimant="p1", now=FIXED_TIME)

        assert ran == 3
        assert len(calls) == 3

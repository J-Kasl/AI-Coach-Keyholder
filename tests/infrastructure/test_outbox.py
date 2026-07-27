"""
tests/infrastructure/test_outbox.py

Tests for infrastructure/outbox.py. Uses database.database.Database's
migrate() for test setup convenience (a real, fully-migrated schema) --
this is a test-only dependency; infrastructure/outbox.py itself never
imports from database/.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from database.database import Database as Repository
from infrastructure.database import Database, apply_transition
from infrastructure.outbox import (
    ClaimedDomainEvent,
    DomainEvent,
    claim_pending_events,
    consume_event,
    has_been_processed,
    mark_processed,
    mark_published,
    write_event,
)

FIXED_TIME = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def db(tmp_path: Path) -> Database:
    """A real, migrated schema (via the repository's migrate()), but the
    returned handle is the generic infrastructure.database.Database --
    outbox.py only ever talks to that, never to the repository."""
    repo = Repository(tmp_path / "test.db")
    repo.migrate(now=FIXED_TIME)
    return repo._core


def _sample_event(event_type: str = "test.thing_happened", occurred_at: datetime = FIXED_TIME) -> DomainEvent:
    return DomainEvent(
        event_type=event_type,
        source_module="test_module",
        payload={"key": "value"},
        occurred_at=occurred_at,
    )


class TestWriteEvent:
    def test_write_event_is_readable_after_commit(self, db: Database) -> None:
        with db.transaction() as tx:
            event_id = write_event(tx, _sample_event())

        with db.transaction() as tx:
            row = tx.fetch_one("SELECT * FROM domain_events WHERE id = ?", (event_id,))
        assert row is not None
        assert row["event_type"] == "test.thing_happened"
        assert row["source_module"] == "test_module"
        assert row["published_at"] is None

    def test_write_event_rolls_back_with_its_transaction(self, db: Database) -> None:
        """Fault injection: the event write must not survive if the
        transaction that produced it later fails."""

        class DeliberateFailure(Exception):
            pass

        with pytest.raises(DeliberateFailure):
            with db.transaction() as tx:
                write_event(tx, _sample_event())
                raise DeliberateFailure("simulated failure after the event write")

        with db.transaction() as tx:
            rows = tx.fetch_all("SELECT * FROM domain_events")
        assert rows == []

    def test_payload_round_trips_through_json(self, db: Database) -> None:
        event = DomainEvent(
            event_type="test.thing_happened",
            source_module="test_module",
            payload={"a": 1, "b": [1, 2, 3], "c": {"nested": True}},
            occurred_at=FIXED_TIME,
        )
        with db.transaction() as tx:
            event_id = write_event(tx, event)

        claimed = claim_pending_events(
            db, claimant="p1", batch_size=10, now=FIXED_TIME, lease_duration=timedelta(minutes=5)
        )
        assert len(claimed) == 1
        assert claimed[0].payload == {"a": 1, "b": [1, 2, 3], "c": {"nested": True}}

    def test_write_event_via_apply_transition_events_slot(self, db: Database) -> None:
        """The intended real usage: events= receives write_event as its callable."""
        result = apply_transition(
            db,
            write=lambda tx, _state: "some-result",
            events=lambda tx, _state, _result: write_event(tx, _sample_event()),
        )
        assert result == "some-result"
        with db.transaction() as tx:
            rows = tx.fetch_all("SELECT * FROM domain_events")
        assert len(rows) == 1


class TestClaimPendingEvents:
    def test_claims_unclaimed_events(self, db: Database) -> None:
        with db.transaction() as tx:
            write_event(tx, _sample_event())

        claimed = claim_pending_events(
            db, claimant="p1", batch_size=10, now=FIXED_TIME, lease_duration=timedelta(minutes=5)
        )
        assert len(claimed) == 1
        assert isinstance(claimed[0], ClaimedDomainEvent)

    def test_respects_batch_size(self, db: Database) -> None:
        with db.transaction() as tx:
            for _ in range(5):
                write_event(tx, _sample_event())

        claimed = claim_pending_events(
            db, claimant="p1", batch_size=2, now=FIXED_TIME, lease_duration=timedelta(minutes=5)
        )
        assert len(claimed) == 2

    def test_does_not_reclaim_an_active_claim(self, db: Database) -> None:
        with db.transaction() as tx:
            write_event(tx, _sample_event())

        first = claim_pending_events(
            db, claimant="p1", batch_size=10, now=FIXED_TIME, lease_duration=timedelta(minutes=5)
        )
        second = claim_pending_events(
            db, claimant="p2", batch_size=10, now=FIXED_TIME + timedelta(seconds=1),
            lease_duration=timedelta(minutes=5),
        )
        assert len(first) == 1
        assert second == []

    def test_reclaims_after_lease_expires(self, db: Database) -> None:
        with db.transaction() as tx:
            write_event(tx, _sample_event())

        first = claim_pending_events(
            db, claimant="p1", batch_size=10, now=FIXED_TIME, lease_duration=timedelta(minutes=5)
        )
        # p1 crashed and never published -- its lease expires
        later = FIXED_TIME + timedelta(minutes=10)
        second = claim_pending_events(
            db, claimant="p2", batch_size=10, now=later, lease_duration=timedelta(minutes=5)
        )
        assert len(first) == 1
        assert len(second) == 1
        assert second[0].id == first[0].id

    def test_never_claims_a_published_event(self, db: Database) -> None:
        with db.transaction() as tx:
            event_id = write_event(tx, _sample_event())
        mark_published(db, [event_id], FIXED_TIME)

        claimed = claim_pending_events(
            db, claimant="p1", batch_size=10, now=FIXED_TIME + timedelta(hours=1),
            lease_duration=timedelta(minutes=5),
        )
        assert claimed == []

    def test_claims_in_created_at_order(self, db: Database) -> None:
        with db.transaction() as tx:
            write_event(tx, _sample_event("test.first", occurred_at=FIXED_TIME))
        with db.transaction() as tx:
            write_event(tx, _sample_event("test.second", occurred_at=FIXED_TIME + timedelta(seconds=1)))

        claimed = claim_pending_events(
            db, claimant="p1", batch_size=10, now=FIXED_TIME + timedelta(minutes=1),
            lease_duration=timedelta(minutes=5),
        )
        assert [c.event_type for c in claimed] == ["test.first", "test.second"]


class TestMarkPublished:
    def test_sets_published_at(self, db: Database) -> None:
        with db.transaction() as tx:
            event_id = write_event(tx, _sample_event())
        mark_published(db, [event_id], FIXED_TIME)

        with db.transaction() as tx:
            row = tx.fetch_one("SELECT published_at FROM domain_events WHERE id = ?", (event_id,))
        assert row["published_at"] is not None

    def test_empty_list_is_a_no_op(self, db: Database) -> None:
        mark_published(db, [], FIXED_TIME)  # must not raise


class TestConsumerDedup:
    def test_has_been_processed_false_initially(self, db: Database) -> None:
        with db.transaction() as tx:
            event_id = write_event(tx, _sample_event())
        with db.transaction() as tx:
            assert has_been_processed(tx, event_id, "some_consumer") is False

    def test_mark_processed_then_has_been_processed_true(self, db: Database) -> None:
        with db.transaction() as tx:
            event_id = write_event(tx, _sample_event())
            mark_processed(tx, event_id, "some_consumer", FIXED_TIME)

        with db.transaction() as tx:
            assert has_been_processed(tx, event_id, "some_consumer") is True

    def test_dedup_is_scoped_per_consumer(self, db: Database) -> None:
        """The same event may be legitimately processed by several
        different consumers -- dedup must not cross-contaminate them."""
        with db.transaction() as tx:
            event_id = write_event(tx, _sample_event())
            mark_processed(tx, event_id, "consumer_a", FIXED_TIME)

        with db.transaction() as tx:
            assert has_been_processed(tx, event_id, "consumer_a") is True
            assert has_been_processed(tx, event_id, "consumer_b") is False


class TestConsumeEvent:
    def test_handler_runs_and_returns_true_on_first_delivery(self, db: Database) -> None:
        with db.transaction() as tx:
            event_id = write_event(tx, _sample_event())
        claimed = claim_pending_events(
            db, claimant="p1", batch_size=10, now=FIXED_TIME, lease_duration=timedelta(minutes=5)
        )
        event = claimed[0]

        calls: list[str] = []
        result = consume_event(
            db, event, consumer_name="my_consumer",
            handler=lambda tx: calls.append(event.id), now=FIXED_TIME,
        )

        assert result is True
        assert calls == [event.id]

    def test_redelivery_is_absorbed_silently(self, db: Database) -> None:
        """At-least-once delivery -> exactly-once effect: a second
        delivery of the same event to the same consumer must not rerun
        the handler."""
        with db.transaction() as tx:
            event_id = write_event(tx, _sample_event())
        claimed = claim_pending_events(
            db, claimant="p1", batch_size=10, now=FIXED_TIME, lease_duration=timedelta(minutes=5)
        )
        event = claimed[0]

        calls: list[str] = []
        consume_event(db, event, consumer_name="my_consumer", handler=lambda tx: calls.append("call"), now=FIXED_TIME)
        second_result = consume_event(
            db, event, consumer_name="my_consumer", handler=lambda tx: calls.append("call"), now=FIXED_TIME
        )

        assert second_result is False
        assert calls == ["call"]  # handler ran exactly once, not twice

    def test_handler_failure_rolls_back_the_processed_marker_too(self, db: Database) -> None:
        """Fault injection: if handler() fails, mark_processed() must
        not have taken effect either -- otherwise a failed reaction
        would be silently treated as successfully processed forever."""
        with db.transaction() as tx:
            event_id = write_event(tx, _sample_event())
        claimed = claim_pending_events(
            db, claimant="p1", batch_size=10, now=FIXED_TIME, lease_duration=timedelta(minutes=5)
        )
        event = claimed[0]

        class HandlerFailure(Exception):
            pass

        def failing_handler(tx) -> None:
            raise HandlerFailure("simulated consumer failure")

        with pytest.raises(HandlerFailure):
            consume_event(db, event, consumer_name="my_consumer", handler=failing_handler, now=FIXED_TIME)

        with db.transaction() as tx:
            assert has_been_processed(tx, event.id, "my_consumer") is False

    def test_different_consumers_each_get_their_own_delivery(self, db: Database) -> None:
        with db.transaction() as tx:
            event_id = write_event(tx, _sample_event())
        claimed = claim_pending_events(
            db, claimant="p1", batch_size=10, now=FIXED_TIME, lease_duration=timedelta(minutes=5)
        )
        event = claimed[0]

        calls: list[str] = []
        result_a = consume_event(db, event, consumer_name="consumer_a", handler=lambda tx: calls.append("a"), now=FIXED_TIME)
        result_b = consume_event(db, event, consumer_name="consumer_b", handler=lambda tx: calls.append("b"), now=FIXED_TIME)

        assert result_a is True
        assert result_b is True
        assert calls == ["a", "b"]

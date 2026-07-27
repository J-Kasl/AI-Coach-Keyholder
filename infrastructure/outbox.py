"""
infrastructure/outbox.py

The shared, domain-agnostic transactional outbox
(`implementation_conventions.md` Section 5) — the one way every module
writes a cross-module event, and the one way every consumer reacts to
one. Contains no knowledge of any specific event type; see
`docs/architecture/domain_events_catalog.md` for the actual registry of
event types this system defines.

Schema lives in `database/migrations/002_domain_events.sql` — this
module owns the behavior, not the DDL, mirroring the same separation
`infrastructure/database.py` already established for the generic
transactional core.

Deliberately does NOT import anything from `database/` (this package
depends on nothing project-specific) — see `_iso`/`_parse_iso` below,
small local duplicates of `database/models.py`'s helpers, rather than a
reverse dependency `infrastructure -> database`.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable

from infrastructure.database import Database, Transaction, apply_transition

__all__ = [
    "DomainEvent",
    "ClaimedDomainEvent",
    "write_event",
    "claim_pending_events",
    "mark_published",
    "has_been_processed",
    "mark_processed",
    "consume_event",
]


# _iso/_parse_iso: thin local aliases for the shared implementation
# (infrastructure/time_format.py) -- kept as private names here so
# every existing call site in this module is unchanged; consolidated
# during the final architecture review pass (Phase 2.7) to remove five
# identical copies of this pair across the codebase.
from infrastructure.time_format import iso as _iso
from infrastructure.time_format import parse_iso as _parse_iso


@dataclass(frozen=True, kw_only=True)
class DomainEvent:
    """
    An event a module wants written to the shared outbox, in the same
    transaction as the state change that caused it — this is exactly
    the shape `infrastructure.database.apply_transition()`'s `events=`
    parameter expects to receive from its caller, e.g.:

        apply_transition(
            db,
            write=lambda tx, _state: ...,
            events=lambda tx, _state, result: write_event(
                tx,
                DomainEvent(
                    event_type="rules.consent_recorded",
                    source_module="database",
                    payload={"rule_id": result[0], "consent_id": result[1]},
                    occurred_at=clock.now(),
                ),
            ),
        )

    `occurred_at` is supplied by the caller, from an injected `Clock` —
    this module never calls `datetime.now()`/`utcnow()` itself, per the
    project-wide convention (`infrastructure/clock.py`).
    """

    event_type: str
    source_module: str
    payload: dict[str, Any] = field(default_factory=dict)
    occurred_at: datetime


@dataclass(frozen=True, kw_only=True)
class ClaimedDomainEvent:
    """A domain_events row as read back by a publisher, after claiming it."""

    id: str
    event_type: str
    source_module: str
    payload: dict[str, Any]
    occurred_at: datetime
    created_at: datetime


def write_event(tx: Transaction, event: DomainEvent) -> str:
    """
    Writes one event to the outbox. Always called from inside an
    already-open `Transaction` — never opens its own — so that the
    event and the state change it describes commit or roll back
    together (`implementation_conventions.md` Section 4).

    Returns the generated event id. A fresh id is always generated
    here, never supplied by the caller: this event is created exactly
    once per successful transaction attempt (a rolled-back attempt
    writes nothing, so a clean retry cannot produce a duplicate row) —
    there is no redelivery path this id needs to protect against on
    the *write* side; redelivery is a concern for the *consumer* side
    (see `has_been_processed`/`mark_processed`), not this function.
    """
    event_id = str(uuid.uuid4())
    now = event.occurred_at  # the row's own created_at == occurred_at unless a caller ever needs them to differ
    tx.execute(
        """
        INSERT INTO domain_events
            (id, event_type, source_module, payload_json, occurred_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            event.event_type,
            event.source_module,
            json.dumps(event.payload),
            _iso(event.occurred_at),
            _iso(now),
        ),
    )
    return event_id


def claim_pending_events(
    db: Database,
    *,
    claimant: str,
    batch_size: int,
    now: datetime,
    lease_duration: timedelta,
) -> list[ClaimedDomainEvent]:
    """
    Claims up to `batch_size` unpublished events for `claimant` to
    deliver — either never-claimed rows, or rows whose previous claim
    has expired (a prior publisher crashed mid-delivery). Claiming and
    reading happen in one transaction, so two concurrent publisher
    processes calling this at the same time can never claim the same
    row (`BEGIN IMMEDIATE` — `infrastructure/database.py` — serializes
    them).

    `claimant` is recorded nowhere today beyond this call's own
    bookkeeping need (there is no per-claimant column) — batch_size and
    lease_duration are what actually matter for correctness; the
    identity of which publisher process did the claiming is not part of
    this system's current design and can be added if a future need
    (e.g. per-publisher metrics) actually arises.
    """

    def write(tx: Transaction, _state: object) -> list[ClaimedDomainEvent]:
        rows = tx.fetch_all(
            """
            SELECT id, event_type, source_module, payload_json, occurred_at, created_at
            FROM domain_events
            WHERE published_at IS NULL
              AND (claimed_at IS NULL OR claim_expires_at <= ?)
            ORDER BY created_at
            LIMIT ?
            """,
            (_iso(now), batch_size),
        )
        if not rows:
            return []

        claim_expires_at = _iso(now + lease_duration)
        ids = [row["id"] for row in rows]
        tx.executemany(
            "UPDATE domain_events SET claimed_at = ?, claim_expires_at = ? WHERE id = ?",
            [(_iso(now), claim_expires_at, event_id) for event_id in ids],
        )

        return [
            ClaimedDomainEvent(
                id=row["id"],
                event_type=row["event_type"],
                source_module=row["source_module"],
                payload=json.loads(row["payload_json"]),
                occurred_at=_parse_iso(row["occurred_at"]),
                created_at=_parse_iso(row["created_at"]),
            )
            for row in rows
        ]

    return apply_transition(db, write=write)


def mark_published(db: Database, event_ids: list[str], now: datetime) -> None:
    """
    Marks a batch of events as successfully handed off to the
    transport layer. `published_at` is set exactly once, ever — this
    function does not need to guard against being called twice for the
    same id, since a publisher only calls it after a successful
    delivery it just performed; calling it again for an
    already-published id is a harmless no-op UPDATE, not a correctness
    concern.
    """
    if not event_ids:
        return

    def write(tx: Transaction, _state: object) -> None:
        tx.executemany(
            "UPDATE domain_events SET published_at = ? WHERE id = ?",
            [(_iso(now), event_id) for event_id in event_ids],
        )

    apply_transition(db, write=write)


def has_been_processed(tx: Transaction, event_id: str, consumer_name: str) -> bool:
    """
    Checked by a consumer, inside its own transaction, before acting on
    a delivered event — the at-least-once-delivery-to-exactly-once-effect
    guard (`implementation_conventions.md` Section 5).
    """
    row = tx.fetch_one(
        "SELECT 1 FROM domain_event_consumers WHERE event_id = ? AND consumer_name = ?",
        (event_id, consumer_name),
    )
    return row is not None


def mark_processed(tx: Transaction, event_id: str, consumer_name: str, now: datetime) -> None:
    """
    Records that `consumer_name` has processed `event_id` — always
    called in the SAME transaction as the consumer's own reaction (see
    `consume_event` below), never as a separate, later step.
    """
    tx.execute(
        "INSERT INTO domain_event_consumers (event_id, consumer_name, processed_at) VALUES (?, ?, ?)",
        (event_id, consumer_name, _iso(now)),
    )


def consume_event(
    db: Database,
    event: ClaimedDomainEvent,
    *,
    consumer_name: str,
    handler: Callable[[Transaction], None],
    now: datetime,
) -> bool:
    """
    The consumption counterpart to `apply_transition` — built directly
    on top of it, not a parallel reimplementation: dedup-check, run the
    consumer's own reaction, and record having processed the event, all
    in one transaction. Returns `True` if `handler` actually ran,
    `False` if this event had already been processed by this consumer
    (a harmless redelivery, silently absorbed rather than reprocessed).
    """

    def load(tx: Transaction) -> bool:
        return has_been_processed(tx, event.id, consumer_name)

    def write(tx: Transaction, already_processed: bool) -> bool:
        if already_processed:
            return False
        handler(tx)
        mark_processed(tx, event.id, consumer_name, now)
        return True

    return apply_transition(db, load=load, write=write)

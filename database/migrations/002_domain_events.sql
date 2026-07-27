-- =============================================================================
-- Migration 002 — Transactional Outbox (Phase 1.4)
-- =============================================================================
-- The shared, domain-agnostic outbox every module writes cross-module
-- events to (implementation_conventions.md Section 5). Owned in code by
-- infrastructure/outbox.py -- this migration only establishes the schema,
-- the same separation already used for infrastructure/database.py's
-- generic Database/Transaction (schema lives in a project migration,
-- domain-agnostic behavior lives in infrastructure/).
--
-- See docs/architecture/domain_events_catalog.md for the full registry
-- of event types this table is expected to carry once real domain
-- modules (Trust Manager, Penalty Engine, ...) exist to write them.
-- =============================================================================

CREATE TABLE IF NOT EXISTS domain_events (
    id              TEXT PRIMARY KEY,
    event_type      TEXT NOT NULL,          -- e.g. 'penalty_window.started' -- see domain_events_catalog.md
    source_module   TEXT NOT NULL,          -- the module whose transaction wrote this event
    payload_json    TEXT NOT NULL,          -- event-specific fields, serialized JSON
    occurred_at     TEXT NOT NULL,          -- when the underlying state change happened (from the caller's Clock)
    created_at      TEXT NOT NULL,          -- when this outbox row itself was written (same transaction as occurred_at's cause)

    -- Claim/publish lifecycle (implementation_conventions.md Section 5):
    -- a publisher claims a batch of unclaimed/expired-claim rows before
    -- delivering them, so multiple concurrent publisher processes never
    -- double-deliver the same row mid-flight.
    claimed_at          TEXT,               -- NULL = not currently claimed by any publisher
    claim_expires_at    TEXT,               -- claim is considered abandoned once this passes
    published_at        TEXT                -- NULL = not yet successfully published; set exactly once
);

CREATE INDEX IF NOT EXISTS idx_domain_events_unpublished
    ON domain_events(published_at, claim_expires_at)
    WHERE published_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_domain_events_type ON domain_events(event_type);
CREATE INDEX IF NOT EXISTS idx_domain_events_occurred ON domain_events(occurred_at);


-- =============================================================================
-- Consumer-side dedup (at-least-once delivery -> exactly-once *effect*)
-- =============================================================================
-- Every consumer, before acting on a delivered event, checks this table
-- inside the SAME transaction as its own reaction, and records having
-- processed the event in that same transaction -- the consumption
-- counterpart to _apply_transition (implementation_conventions.md
-- Section 4), applied to consuming an event rather than producing one.

CREATE TABLE IF NOT EXISTS domain_event_consumers (
    event_id        TEXT NOT NULL REFERENCES domain_events(id),
    consumer_name   TEXT NOT NULL,          -- e.g. 'penalty_engine', 'recovery_plan', ...
    processed_at    TEXT NOT NULL,
    PRIMARY KEY (event_id, consumer_name)
);


-- =============================================================================
-- Seed: record this migration in schema_version
-- =============================================================================
INSERT INTO schema_version (version, applied_at, description)
VALUES (2, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'Transactional outbox: domain_events, domain_event_consumers (Faze 1.4)');

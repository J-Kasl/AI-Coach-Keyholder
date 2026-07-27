-- =============================================================================
-- Migration 006 — System Startup Lease (Phase 2.4)
-- =============================================================================
-- docs/architecture/system_state_machine.md Section 7 (LEASE-1).
-- Single-row table: at most one process instance may hold a live lease
-- at a time, enforced by the atomic UPDATE/INSERT in
-- infrastructure/startup_lease.py, not by application convention.
-- =============================================================================

CREATE TABLE IF NOT EXISTS system_startup_lease (
    id            INTEGER PRIMARY KEY CHECK (id = 1),   -- single-row table, one lease in the whole system
    held_by       TEXT,
    acquired_at    TEXT,
    expires_at     TEXT
);

-- =============================================================================
-- Seed: record this migration in schema_version
-- =============================================================================
INSERT INTO schema_version (version, applied_at, description)
VALUES (6, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'System startup lease table (Faze 2.4)');

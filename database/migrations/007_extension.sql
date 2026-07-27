-- =============================================================================
-- Migration 007 — Extension (Fáze 2.5)
-- =============================================================================
-- docs/architecture/extension_technical_design.md.
-- Additive only (implementation_conventions.md Section 12): adds a
-- column to the existing incident_consumption table (never redefines
-- it) and one new table.
-- =============================================================================

-- EXT-2: same_rule_confirmed_incident_count_in_current_window needs
-- rule_group_id available locally, without a cross-module read back
-- into Trust Manager (which would risk NestedTransactionError from
-- inside a consumer handler -- see system/README.md). SQLite requires
-- a DEFAULT for a NOT NULL column added via ALTER TABLE; the default
-- below is never actually relied on by any code path added in this
-- migration -- every INSERT from this point forward supplies a real
-- rule_group_id explicitly.
ALTER TABLE incident_consumption ADD COLUMN rule_group_id TEXT NOT NULL DEFAULT '';


-- Append-only (EXT-7: explanation always required; EXT-9: written in
-- the same transaction as extensions_hours, never as a separate step).
CREATE TABLE IF NOT EXISTS extension_decisions (
    id                       TEXT PRIMARY KEY,
    created_at                TEXT NOT NULL,
    incident_id                TEXT NOT NULL,
    penalty_window_id           TEXT NOT NULL REFERENCES penalty_windows(id),

    eligible                     INTEGER NOT NULL,
    eligibility_reason            TEXT NOT NULL,

    base_hours                     REAL,          -- NULL iff not eligible
    mitigation_hours                REAL NOT NULL,
    uncapped_hours                   REAL,         -- NULL iff not eligible
    assigned_hours                    REAL NOT NULL,
    capacity_limited                   INTEGER NOT NULL,   -- EXT-6: tracked separately from `eligible`, never conflated

    explanation                         TEXT NOT NULL       -- EXT-7
);

CREATE INDEX IF NOT EXISTS idx_extension_decisions_window ON extension_decisions(penalty_window_id);
CREATE INDEX IF NOT EXISTS idx_extension_decisions_incident ON extension_decisions(incident_id);


-- =============================================================================
-- Seed: zápis této migrace do schema_version
-- =============================================================================
INSERT INTO schema_version (version, applied_at, description)
VALUES (7, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'Extension: extension_decisions, incident_consumption.rule_group_id (Faze 2.5)');

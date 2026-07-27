-- =============================================================================
-- Migration 009 — Recovery Credit Integration (Phase 2.7)
-- =============================================================================
-- docs/architecture/penalty_window_technical_design.md Section 3.4,
-- applying recovery_plan_technical_design.md Section 6.
-- Additive only (implementation_conventions.md Section 12).
-- =============================================================================

-- Denormalized running total (I3-adjacent), updated in the same
-- transaction as each recovery_credit_ledger insert -- never
-- independently recomputed elsewhere.
ALTER TABLE penalty_windows ADD COLUMN recovery_credits_earned_hours REAL NOT NULL DEFAULT 0;


-- Append-only (I26 primary guarantee: UNIQUE(completion_id) -- a given
-- RecoveryTaskCompletion is processed at most once, ever, regardless of
-- outcome). Always written, eligible-for-credit or not (mirrors
-- ExtensionDecision's own "always written" discipline).
CREATE TABLE IF NOT EXISTS recovery_credit_decisions (
    id                    TEXT PRIMARY KEY,
    created_at             TEXT NOT NULL,
    completion_id            TEXT NOT NULL UNIQUE,     -- I26 primary
    penalty_window_id          TEXT NOT NULL REFERENCES penalty_windows(id),

    proposed_hours               REAL NOT NULL,
    credited_hours                 REAL NOT NULL,       -- may be 0
    capacity_limited                 INTEGER NOT NULL,   -- separate from "was this eligible" -- always eligible here, only capacity varies

    explanation                        TEXT NOT NULL      -- required, non-empty regardless of credited_hours
);

CREATE INDEX IF NOT EXISTS idx_recovery_credit_decisions_window ON recovery_credit_decisions(penalty_window_id);


-- Append-only. Only written when credited_hours > 0 (the decision
-- record above is the complete audit trail regardless).
-- source_completion_id UNIQUE is I26's secondary guarantee.
CREATE TABLE IF NOT EXISTS recovery_credit_ledger (
    id                       TEXT PRIMARY KEY,
    penalty_window_id         TEXT NOT NULL REFERENCES penalty_windows(id),
    credited_hours              REAL NOT NULL,
    source_completion_id          TEXT UNIQUE,          -- I26 secondary (NULL entries, if any future source besides task completion exists, are each distinct under standard SQL UNIQUE semantics)
    created_at                     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_recovery_credit_ledger_window ON recovery_credit_ledger(penalty_window_id);


-- =============================================================================
-- Seed: record this migration in schema_version
-- =============================================================================
INSERT INTO schema_version (version, applied_at, description)
VALUES (9, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'Recovery Credit integration: recovery_credit_decisions, recovery_credit_ledger, penalty_windows.recovery_credits_earned_hours (Faze 2.7)');

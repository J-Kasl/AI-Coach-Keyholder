-- =============================================================================
-- Migration 010 — Goal Management, Slice 1 (Phase 2.9)
-- =============================================================================
-- docs/architecture/goal_technical_design.md Sections 2-5, 8-9, 13.
-- See goal_management/README.md for exactly what this slice covers.
--
-- GOAL-1: no table here is ever referenced by, or writes to,
-- penalty_windows/freeze_periods/incidents — enforced structurally
-- (this module has no import of trust_manager/penalty_engine at all),
-- not merely by schema convention.
-- =============================================================================

CREATE TABLE IF NOT EXISTS goals (
    goal_group_id            TEXT PRIMARY KEY,
    -- Deliberately NOT a FOREIGN KEY to goal_versions(id): the two rows
    -- (this one and its first GoalVersion) are always written in the
    -- same transaction, but goal_versions.goal_group_id itself DOES
    -- reference this table -- a real chicken-and-egg at creation time.
    -- Resolved by inserting this row first (with the version's
    -- pre-generated id) and the GoalVersion row second; application-
    -- enforced, not DB-enforced, consistent with both rows only ever
    -- being written together (see GoalManager.create_goal()).
    current_version_id        TEXT NOT NULL,
    status                      TEXT NOT NULL,     -- 'active' | 'paused' | 'completed' | 'abandoned' | 'replaced'
    created_at                   TEXT NOT NULL,
    status_changed_at             TEXT NOT NULL,
    replaces_goal_group_id          TEXT REFERENCES goals(goal_group_id),  -- populated only if this Goal REPLACED an earlier one (2.4)
    archived_at                      TEXT     -- independent of status (3.3, GOAL-11)
);


-- Append-only (GOAL-5).
CREATE TABLE IF NOT EXISTS goal_versions (
    id                    TEXT PRIMARY KEY,
    goal_group_id          TEXT NOT NULL REFERENCES goals(goal_group_id),
    version                  INTEGER NOT NULL,
    title                      TEXT NOT NULL,
    target_description          TEXT NOT NULL,
    trust_domain                  TEXT NOT NULL,     -- fixed at creation (11.1); not changeable by an adaptation/replacement in this slice
    created_at                     TEXT NOT NULL,
    created_via                     TEXT NOT NULL,     -- 'user_proposed' | 'coach_proposed_user_approved' | 'coach_initial_setup'
    adaptation_reason                 TEXT,              -- REQUIRED if version > 1 (GOAL-5) -- enforced in code, not a CHECK constraint (simpler than a conditional CHECK; already unit tested)
    supersedes_id                      TEXT REFERENCES goal_versions(id)
);

CREATE INDEX IF NOT EXISTS idx_goal_versions_group ON goal_versions(goal_group_id);


-- Append-only (GOAL-4).
CREATE TABLE IF NOT EXISTS goal_evidence (
    id                    TEXT PRIMARY KEY,
    goal_group_id          TEXT NOT NULL REFERENCES goals(goal_group_id),
    goal_version_id          TEXT NOT NULL REFERENCES goal_versions(id),
    period_start                TEXT NOT NULL,
    period_end                   TEXT NOT NULL,
    outcome                        TEXT NOT NULL,     -- 'met' | 'partially_met' | 'missed' (GoalOutcome, 2.6)
    observed_progress                TEXT NOT NULL,
    source                             TEXT NOT NULL,     -- 'check_in' | 'user_report' | 'system_derived'
    created_at                          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_goal_evidence_group ON goal_evidence(goal_group_id);


-- Append-only. GOAL-3: triggering_evidence_ids must be non-empty --
-- enforced in code (the JSON array is validated before INSERT), same
-- reasoning as adaptation_reason above.
CREATE TABLE IF NOT EXISTS goal_evaluations (
    id                            TEXT PRIMARY KEY,
    goal_group_id                  TEXT NOT NULL REFERENCES goals(goal_group_id),
    created_at                       TEXT NOT NULL,
    triggering_evidence_ids_json       TEXT NOT NULL,
    findings                             TEXT NOT NULL,
    proposed_intervention                  TEXT NOT NULL,     -- GoalInterventionType
    proposed_intervention_detail             TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_goal_evaluations_group ON goal_evaluations(goal_group_id);


CREATE TABLE IF NOT EXISTS goal_change_proposals (
    id                    TEXT PRIMARY KEY,
    evaluation_id          TEXT REFERENCES goal_evaluations(id),   -- NULL if user-initiated rather than Coach-proposed
    goal_group_id            TEXT NOT NULL REFERENCES goals(goal_group_id),
    proposed_change            TEXT NOT NULL,     -- GoalInterventionType
    proposal_expires_at          TEXT NOT NULL,
    status                          TEXT NOT NULL,     -- GoalProposalStatus: 'pending' | 'accepted' | 'declined' | 'expired'
    created_at                       TEXT NOT NULL,
    resolved_at                       TEXT
);

CREATE INDEX IF NOT EXISTS idx_goal_change_proposals_group ON goal_change_proposals(goal_group_id);
CREATE INDEX IF NOT EXISTS idx_goal_change_proposals_status ON goal_change_proposals(status);


-- Append-only, immutable (GOAL-6: acceptance always applies exactly
-- this recorded content, never content reconstructed at acceptance
-- time). 1:1 with its proposal.
CREATE TABLE IF NOT EXISTS goal_change_proposal_contents (
    id                    TEXT PRIMARY KEY,
    proposal_id             TEXT NOT NULL UNIQUE REFERENCES goal_change_proposals(id),
    proposed_title             TEXT,
    proposed_target_description  TEXT,
    proposed_replacement_goal_group_id TEXT,
    reason                          TEXT NOT NULL
);


-- =============================================================================
-- Seed: record this migration in schema_version
-- =============================================================================
INSERT INTO schema_version (version, applied_at, description)
VALUES (10, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'Goal Management Slice 1: goals, goal_versions, goal_evidence, goal_evaluations, goal_change_proposals(_contents) (Phase 2.9)');

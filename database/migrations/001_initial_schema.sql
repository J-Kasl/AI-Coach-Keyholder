-- =============================================================================
-- AI Coach & Keyholder — Initial Schema (migration 001)
-- =============================================================================
-- Hybrid approach: important fields are normalized (IDs, timestamps,
-- versions, FK relationships); complex/variable data (reasoning,
-- factors, raw_data) is stored as JSON in TEXT columns. SQLite's JSON1
-- extension ships with the standard distribution, so JSON columns can
-- still be queried (json_extract) during audits.
--
-- Conventions:
--   - all IDs are TEXT (UUID4 strings generated in Python)
--   - all timestamps are TEXT in ISO 8601 (UTC), e.g. 2026-07-23T10:00:00Z
--   - *_json columns hold serialized JSON; validation happens in Python
--   - engine_version is always TEXT (e.g. "coach_engine@1.2.0")
-- =============================================================================

PRAGMA foreign_keys = ON;

-- -----------------------------------------------------------------------
-- Schema versioning — enables sequential migration application and
-- auditing of which schema version the runtime is currently on.
-- -----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS schema_version (
    version         INTEGER PRIMARY KEY,
    applied_at      TEXT NOT NULL,
    description     TEXT NOT NULL
);


-- =============================================================================
-- RULES & CONSENT
-- =============================================================================
-- Rules are versioned: every rule change creates a new row with the
-- same rule_group_id, never an in-place update. This gives a full
-- history of "what applied when" and is required for auditability and
-- for the Consent & Control principle (philosophy.md, 2.5) — consent
-- is tied to a specific rule version.
-- -----------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS rules (
    id              TEXT PRIMARY KEY,          -- UUID of this specific version
    rule_group_id   TEXT NOT NULL,             -- stable ID across versions of the same rule
    version         INTEGER NOT NULL,          -- 1, 2, 3... within rule_group_id
    title           TEXT NOT NULL,
    description     TEXT NOT NULL,
    category        TEXT NOT NULL,             -- e.g. "training", "sleep", "study"
    parameters_json TEXT NOT NULL DEFAULT '{}',-- structured rule parameters

    is_active       INTEGER NOT NULL DEFAULT 1,-- 0/1 -- currently active version?
    supersedes_id   TEXT,                      -- FK to the previous version (NULL for version 1)

    created_at      TEXT NOT NULL,
    created_by      TEXT NOT NULL DEFAULT 'user', -- 'user' | 'ai_proposal'
    is_critical     TEXT NOT NULL DEFAULT 0,       -- 0/1 -- always requires approval regardless of impact score

    FOREIGN KEY (supersedes_id) REFERENCES rules(id)
);

CREATE INDEX IF NOT EXISTS idx_rules_group ON rules(rule_group_id);
CREATE INDEX IF NOT EXISTS idx_rules_active ON rules(is_active);


-- Consent log — a separate, append-only history of consent decisions.
-- Never edited, only new rows added. This is the source of truth for
-- "when, and to exactly what, did the user consent"
-- (philosophy.md 2.5, Consent & Control).
CREATE TABLE IF NOT EXISTS consent_log (
    id              TEXT PRIMARY KEY,
    created_at      TEXT NOT NULL,

    target_type     TEXT NOT NULL,             -- 'rule' | 'philosophy' | 'trust_algorithm' | 'reward_algorithm' | 'integration'
    target_id       TEXT,                      -- FK to rules.id etc.; NULL if it concerns the whole (e.g. philosophy.md)
    target_version  TEXT,                      -- e.g. the version of philosophy.md ("1.1")

    action          TEXT NOT NULL,             -- 'approved' | 'rejected' | 'revoked'
    decision_result_id TEXT,                   -- if consent arose from a proposed decision

    explanation_shown TEXT,                    -- exactly what was explained to the user before the decision (audit trail)
    user_comment    TEXT,                      -- optional user note

    FOREIGN KEY (decision_result_id) REFERENCES decision_results(id)
);

CREATE INDEX IF NOT EXISTS idx_consent_target ON consent_log(target_type, target_id);


-- =============================================================================
-- CONTEXT SNAPSHOT
-- =============================================================================

CREATE TABLE IF NOT EXISTS context_snapshots (
    id                      TEXT PRIMARY KEY,
    created_at              TEXT NOT NULL,
    engine_version          TEXT NOT NULL,

    overall_confidence      REAL NOT NULL,
    data_freshness_hours    REAL NOT NULL,

    context_factors_json    TEXT NOT NULL DEFAULT '[]',   -- list[ContextFactor]
    relevant_patterns_json  TEXT NOT NULL DEFAULT '[]'    -- list[RelevantPattern]
);

CREATE INDEX IF NOT EXISTS idx_context_created ON context_snapshots(created_at);


-- =============================================================================
-- COACH ASSESSMENT
-- =============================================================================

CREATE TABLE IF NOT EXISTS coach_assessments (
    id                      TEXT PRIMARY KEY,
    created_at              TEXT NOT NULL,
    engine_version          TEXT NOT NULL,
    context_snapshot_id     TEXT NOT NULL,

    recommendation          TEXT NOT NULL,
    reasoning               TEXT NOT NULL,
    confidence              REAL NOT NULL,

    risk_direction          TEXT NOT NULL DEFAULT 'none', -- 'overload' | 'stagnation' | 'none'
    sustainability_score    REAL NOT NULL,
    supporting_factors_json TEXT NOT NULL DEFAULT '[]',   -- list[str]

    FOREIGN KEY (context_snapshot_id) REFERENCES context_snapshots(id)
);

CREATE INDEX IF NOT EXISTS idx_coach_context ON coach_assessments(context_snapshot_id);


-- =============================================================================
-- KEYHOLDER ASSESSMENT (+ trust/reward as nested JSON state)
-- =============================================================================

CREATE TABLE IF NOT EXISTS keyholder_assessments (
    id                      TEXT PRIMARY KEY,
    created_at              TEXT NOT NULL,
    engine_version          TEXT NOT NULL,
    context_snapshot_id     TEXT NOT NULL,

    recommendation          TEXT NOT NULL,
    reasoning               TEXT NOT NULL,
    confidence              REAL NOT NULL,

    consistency_score       REAL NOT NULL,
    trust_state_json        TEXT NOT NULL DEFAULT '{}',  -- TrustState
    reward_state_json       TEXT NOT NULL DEFAULT '{}',  -- RewardState
    rule_relevance_json     TEXT NOT NULL DEFAULT '[]',  -- list[rule_group_id]

    FOREIGN KEY (context_snapshot_id) REFERENCES context_snapshots(id)
);

CREATE INDEX IF NOT EXISTS idx_keyholder_context ON keyholder_assessments(context_snapshot_id);


-- History of trust_score as its own time series — kept separate from
-- keyholder_assessments.trust_state_json, since we want an easily
-- queryable trend over time without having to parse JSON across the
-- whole assessment history.
CREATE TABLE IF NOT EXISTS trust_history (
    id              TEXT PRIMARY KEY,
    recorded_at     TEXT NOT NULL,
    trust_score     REAL NOT NULL,
    reason          TEXT NOT NULL,              -- human-readable reason for the change
    keyholder_assessment_id TEXT,

    FOREIGN KEY (keyholder_assessment_id) REFERENCES keyholder_assessments(id)
);

CREATE INDEX IF NOT EXISTS idx_trust_history_time ON trust_history(recorded_at);


-- =============================================================================
-- DECISION RESULT
-- =============================================================================

CREATE TABLE IF NOT EXISTS decision_results (
    id                      TEXT PRIMARY KEY,
    created_at              TEXT NOT NULL,
    engine_version          TEXT NOT NULL,

    context_snapshot_id     TEXT NOT NULL,
    coach_assessment_id     TEXT NOT NULL,
    keyholder_assessment_id TEXT NOT NULL,

    final_decision          TEXT NOT NULL,
    resolution_method       TEXT NOT NULL,      -- 'rule_based' | 'weighted_score' | 'llm_arbitration'

    impact_score            REAL NOT NULL,
    impact_is_significant   INTEGER NOT NULL,   -- 0/1
    impact_factors_json     TEXT NOT NULL DEFAULT '{}',  -- contributing_factors dict

    -- Two-layer requires_user_approval (see design discussion): hard rules OR impact score
    is_critical_change      INTEGER NOT NULL DEFAULT 0,  -- 0/1 -- critical/rule/safety/philosophy change
    requires_user_approval  INTEGER NOT NULL,   -- 0/1 -- resulting field: is_critical_change OR impact.is_significant (+ safety override)
    safety_override         INTEGER NOT NULL DEFAULT 0,  -- 0/1 -- safety layer overrode the normal evaluation (philosophy.md 2.3)

    explanation              TEXT,               -- generated only if the decision is significant
    approval_status           TEXT NOT NULL DEFAULT 'not_required', -- 'not_required' | 'pending' | 'approved' | 'rejected'

    FOREIGN KEY (context_snapshot_id) REFERENCES context_snapshots(id),
    FOREIGN KEY (coach_assessment_id) REFERENCES coach_assessments(id),
    FOREIGN KEY (keyholder_assessment_id) REFERENCES keyholder_assessments(id)
);

CREATE INDEX IF NOT EXISTS idx_decision_context ON decision_results(context_snapshot_id);
CREATE INDEX IF NOT EXISTS idx_decision_approval ON decision_results(approval_status);
CREATE INDEX IF NOT EXISTS idx_decision_created ON decision_results(created_at);


-- =============================================================================
-- OBSERVATIONS (write-only from the runtime's perspective; only the
-- audit export reads them)
-- =============================================================================

CREATE TABLE IF NOT EXISTS observations (
    id                  TEXT PRIMARY KEY,
    created_at          TEXT NOT NULL,

    observation_type    TEXT NOT NULL,          -- 'decision_made' | 'perspective_conflict' |
                                                  -- 'unexpected_outcome' | 'recurring_pattern' | 'estimation_error'
    related_decision_id TEXT,

    description         TEXT NOT NULL,
    raw_data_json        TEXT NOT NULL DEFAULT '{}',
    flagged_for_review   INTEGER NOT NULL DEFAULT 0,  -- 0/1

    -- filled in by the audit export during processing; the runtime
    -- never reads or sets these
    reviewed_at          TEXT,
    review_notes          TEXT,

    FOREIGN KEY (related_decision_id) REFERENCES decision_results(id)
);

CREATE INDEX IF NOT EXISTS idx_observations_type ON observations(observation_type);
CREATE INDEX IF NOT EXISTS idx_observations_flagged ON observations(flagged_for_review);
CREATE INDEX IF NOT EXISTS idx_observations_created ON observations(created_at);


-- =============================================================================
-- CONVERSATION LOG (short-term memory / raw message history)
-- =============================================================================
-- This is the raw message log for the Discord bot and short-term
-- memory. Long-term memory (extracted insights, patterns) will be
-- handled by pattern_engine / memory_engine on top of an embeddings
-- layer -- to be added in a later migration once its concrete shape is
-- clear (Phase 4).
-- -----------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS conversation_messages (
    id              TEXT PRIMARY KEY,
    created_at      TEXT NOT NULL,
    role            TEXT NOT NULL,          -- 'user' | 'assistant' | 'system'
    content         TEXT NOT NULL,
    discord_channel_id TEXT,
    discord_message_id TEXT,

    -- optional link to a decision, if the message arose in connection with one
    related_decision_id TEXT,

    FOREIGN KEY (related_decision_id) REFERENCES decision_results(id)
);

CREATE INDEX IF NOT EXISTS idx_conversation_created ON conversation_messages(created_at);


-- =============================================================================
-- Seed: record this migration in schema_version
-- =============================================================================
INSERT INTO schema_version (version, applied_at, description)
VALUES (1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'Initial schema: rules, consent_log, context/coach/keyholder/decision, observations, conversation_messages');

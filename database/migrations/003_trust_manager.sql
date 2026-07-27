-- =============================================================================
-- Migration 003 — Trust Manager, Slice 1 (Fáze 2.1)
-- =============================================================================
-- Domain Registry + Domain State + Incident/Confirmation/Severity model,
-- per docs/architecture/trust_manager_technical_design.md Sections 2.1,
-- 2.2, 2.8, 2.10, 5.1-5.4, 13, 14.
--
-- DEFERRED to a later slice (see trust_manager/README.md for the exact
-- boundary): TrustEvidenceDispute (2.5), TrustRecalculation/score
-- recalculation pipeline (2.6, 3.x), OverallTrustReport (2.7, 4),
-- Manual Review restricted operations (2.9), Goal Accountability
-- Assessment integration (15). trust_evidence exists now (TI23 requires
-- it to be written atomically with confirmation reaching CONFIRMED),
-- but nothing yet recalculates trust_domain_state.score/confidence FROM
-- it -- that is the deferred recalculation pipeline's job.
-- =============================================================================

CREATE TABLE IF NOT EXISTS trust_domains (
    domain_id                  TEXT PRIMARY KEY,
    display_name                TEXT NOT NULL,
    description                  TEXT NOT NULL,
    is_active                    INTEGER NOT NULL DEFAULT 1,
    created_at                   TEXT NOT NULL,
    created_via_consent_id       TEXT NOT NULL,     -- TI1: never created without consent
    deactivated_at               TEXT,
    deactivated_via_consent_id   TEXT,
    initial_score_override       REAL,
    initial_confidence_override  REAL
);

CREATE TABLE IF NOT EXISTS trust_domain_state (
    domain_id               TEXT PRIMARY KEY REFERENCES trust_domains(domain_id),
    score                    REAL NOT NULL,
    confidence               REAL NOT NULL,
    trend                    TEXT NOT NULL,          -- 'improving' | 'declining' | 'stable'
    last_recalculated_at     TEXT NOT NULL,
    last_relevant_event_at   TEXT
);


-- =============================================================================
-- Incident (owned entirely by the Trust Manager -- confirmation, assessment,
-- and all descriptive fields; TI22). The Penalty Engine references this
-- row's id in its OWN incident_consumption table but never duplicates
-- its shape and never writes here.
-- =============================================================================

CREATE TABLE IF NOT EXISTS incidents (
    id              TEXT PRIMARY KEY,
    created_at      TEXT NOT NULL,
    rule_group_id   TEXT NOT NULL,
    trust_domain    TEXT NOT NULL REFERENCES trust_domains(domain_id),
    confirmation    TEXT NOT NULL,          -- IncidentConfirmation -- denormalized from the latest ConfirmationRecord
    description     TEXT NOT NULL,

    -- IncidentEvidence -- the structured facts, always present from registration (5.1)
    evidence_impact                        TEXT NOT NULL,   -- ImpactLevel
    evidence_intentionality                 TEXT NOT NULL,   -- IntentAssessment
    evidence_breach_directness              TEXT NOT NULL,   -- BreachDirectness
    evidence_confidence                      TEXT NOT NULL,   -- EvidenceConfidenceLevel
    evidence_repetition_count                INTEGER NOT NULL,
    evidence_repetition_window_days          INTEGER NOT NULL,
    evidence_repetition_source_ids_json       TEXT NOT NULL,

    -- IncidentAssessment -- NULL until confirmation reaches CONFIRMED (TI15)
    assessment_intrinsic_severity                    TEXT,   -- SeverityTier
    assessment_cooperation_self_disclosed             INTEGER,
    assessment_cooperation_active_resolution          INTEGER,
    assessment_cooperation_notes                       TEXT,
    assessment_rubric_explanation                      TEXT
);

CREATE INDEX IF NOT EXISTS idx_incidents_confirmation ON incidents(confirmation);
CREATE INDEX IF NOT EXISTS idx_incidents_domain ON incidents(trust_domain);
CREATE INDEX IF NOT EXISTS idx_incidents_rule_group ON incidents(rule_group_id);


CREATE TABLE IF NOT EXISTS confirmation_records (
    id                      TEXT PRIMARY KEY,
    incident_id             TEXT NOT NULL REFERENCES incidents(id),
    created_at              TEXT NOT NULL,
    previous_confirmation   TEXT NOT NULL,
    new_confirmation        TEXT NOT NULL,
    source                  TEXT NOT NULL,     -- ConfirmationSource
    evidence_description     TEXT NOT NULL      -- TI16: always populated
);

CREATE INDEX IF NOT EXISTS idx_confirmation_records_incident ON confirmation_records(incident_id);


-- =============================================================================
-- TrustEvidence -- append-only (TI3: no UPDATE/DELETE path exists at the
-- access layer for this table at all).
-- =============================================================================

CREATE TABLE IF NOT EXISTS trust_evidence (
    id                    TEXT PRIMARY KEY,
    domain_id              TEXT NOT NULL REFERENCES trust_domains(domain_id),
    created_at              TEXT NOT NULL,
    evidence_type           TEXT NOT NULL,     -- EvidenceType
    source_entity_type       TEXT NOT NULL,     -- 'incident' | 'recovery_credit_ledger' | 'manual_review' | ...
    source_entity_id         TEXT NOT NULL,
    raw_weight               REAL NOT NULL,     -- signed, BEFORE confidence scaling
    evidence_confidence       REAL NOT NULL,
    explanation               TEXT NOT NULL,

    -- TI25 (goal_technical_design.md Section 15 groundwork, not yet wired):
    -- one raw source fact produces at most one piece of evidence of a
    -- given type -- this is what makes redelivery of an upstream event
    -- safe, for every evidence-producing integration this system will
    -- ever have, not only the deferred Goal one.
    UNIQUE(source_entity_type, source_entity_id, evidence_type)
);

CREATE INDEX IF NOT EXISTS idx_trust_evidence_domain ON trust_evidence(domain_id);


-- =============================================================================
-- Seed: zápis této migrace do schema_version
-- =============================================================================
INSERT INTO schema_version (version, applied_at, description)
VALUES (3, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'Trust Manager Slice 1: domain registry/state, Incident/Confirmation/severity model, TrustEvidence (Faze 2.1)');

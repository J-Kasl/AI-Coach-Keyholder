-- =============================================================================
-- Migration 004 — Trust Manager, Slice 2: Score Recalculation Pipeline (Fáze 2.2)
-- =============================================================================
-- docs/architecture/trust_manager_technical_design.md Sections 2.6, 3.1-3.6.
-- See trust_manager/README.md for exactly what this slice covers.
-- =============================================================================

CREATE TABLE IF NOT EXISTS trust_recalculations (
    id                     TEXT PRIMARY KEY,
    domain_id               TEXT NOT NULL REFERENCES trust_domains(domain_id),
    created_at              TEXT NOT NULL,
    previous_score           REAL NOT NULL,
    new_score                REAL NOT NULL,
    previous_confidence      REAL NOT NULL,
    new_confidence           REAL NOT NULL,
    triggered_by             TEXT NOT NULL,     -- 'incident' | 'window_completion' | 'scheduled_review' | 'manual'
    explanation               TEXT NOT NULL      -- TI10 -- always required
);

CREATE INDEX IF NOT EXISTS idx_trust_recalculations_domain ON trust_recalculations(domain_id);

-- TI4: a piece of evidence is consumed AT MOST ONCE, ever -- UNIQUE on
-- evidence_id alone (not a composite key) is what enforces this, not
-- merely (recalculation_id, evidence_id).
CREATE TABLE IF NOT EXISTS trust_recalculation_evidence (
    recalculation_id    TEXT NOT NULL REFERENCES trust_recalculations(id),
    evidence_id           TEXT NOT NULL UNIQUE REFERENCES trust_evidence(id),
    effective_weight       REAL NOT NULL,    -- raw_weight * evidence_confidence, capped (3.3, TI9)
    created_at             TEXT NOT NULL,
    PRIMARY KEY (recalculation_id, evidence_id)
);

CREATE INDEX IF NOT EXISTS idx_trust_recalc_evidence_recalc ON trust_recalculation_evidence(recalculation_id);


-- =============================================================================
-- Seed: zápis této migrace do schema_version
-- =============================================================================
INSERT INTO schema_version (version, applied_at, description)
VALUES (4, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'Trust Manager Slice 2: score recalculation pipeline (Faze 2.2)');

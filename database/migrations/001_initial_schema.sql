-- =============================================================================
-- AI Coach & Keyholder — Initial Schema (migration 001)
-- =============================================================================
-- Hybridní přístup: důležitá pole normalizovaná (ID, timestampy, verze, FK
-- vazby), komplexní/proměnlivá data (reasoning, faktory, raw_data) jako JSON
-- v TEXT sloupcích. SQLite JSON1 extension je součástí standardní distribuce,
-- takže lze nad JSON sloupci i dotazovat (json_extract) při auditech.
--
-- Konvence:
--   - všechny ID jsou TEXT (UUID4 stringy generované v Pythonu)
--   - všechny timestampy jsou TEXT v ISO 8601 (UTC), např. 2026-07-23T10:00:00Z
--   - *_json sloupce obsahují serializovaný JSON, validace probíhá v Pythonu
--   - engine_version je vždy TEXT (např. "coach_engine@1.2.0")
-- =============================================================================

PRAGMA foreign_keys = ON;

-- -----------------------------------------------------------------------
-- Schema versioning — umožňuje sekvenční aplikaci migrací a audit toho,
-- na jaké verzi schématu runtime aktuálně běží.
-- -----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS schema_version (
    version         INTEGER PRIMARY KEY,
    applied_at      TEXT NOT NULL,
    description     TEXT NOT NULL
);


-- =============================================================================
-- RULES & CONSENT
-- =============================================================================
-- Pravidla jsou verzovaná: každá změna pravidla vytváří nový řádek se stejným
-- rule_group_id, ne update na místě. To dává plnou historii "co platilo kdy"
-- a je to nutné pro auditovatelnost a pro Consent & Control princip
-- (philosophy.md, 2.5) — souhlas se váže na konkrétní verzi pravidla.
-- -----------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS rules (
    id              TEXT PRIMARY KEY,          -- UUID této konkrétní verze
    rule_group_id   TEXT NOT NULL,             -- stabilní ID napříč verzemi téhož pravidla
    version         INTEGER NOT NULL,          -- 1, 2, 3... v rámci rule_group_id
    title           TEXT NOT NULL,
    description     TEXT NOT NULL,
    category        TEXT NOT NULL,             -- např. "training", "sleep", "study"
    parameters_json TEXT NOT NULL DEFAULT '{}',-- strukturované parametry pravidla

    is_active       INTEGER NOT NULL DEFAULT 1,-- 0/1 — aktuálně platná verze?
    supersedes_id   TEXT,                      -- FK na předchozí verzi (NULL u verze 1)

    created_at      TEXT NOT NULL,
    created_by      TEXT NOT NULL DEFAULT 'user', -- 'user' | 'ai_proposal'
    is_critical     TEXT NOT NULL DEFAULT 0,       -- 0/1 — vždy vyžaduje schválení bez ohledu na impact score

    FOREIGN KEY (supersedes_id) REFERENCES rules(id)
);

CREATE INDEX IF NOT EXISTS idx_rules_group ON rules(rule_group_id);
CREATE INDEX IF NOT EXISTS idx_rules_active ON rules(is_active);


-- Consent log — samostatná, append-only historie souhlasů. Nikdy se needituje,
-- pouze přidávají nové záznamy. Toto je zdroj pravdy pro "kdy a k čemu uživatel
-- konkrétně souhlasil" (philosophy.md 2.5, Consent & Control).
CREATE TABLE IF NOT EXISTS consent_log (
    id              TEXT PRIMARY KEY,
    created_at      TEXT NOT NULL,

    target_type     TEXT NOT NULL,             -- 'rule' | 'philosophy' | 'trust_algorithm' | 'reward_algorithm' | 'integration'
    target_id       TEXT,                      -- FK na rules.id apod., NULL pokud se týká celku (např. philosophy.md)
    target_version  TEXT,                      -- např. verze philosophy.md ("1.1")

    action          TEXT NOT NULL,             -- 'approved' | 'rejected' | 'revoked'
    decision_result_id TEXT,                   -- pokud souhlas vznikl v návaznosti na navržené rozhodnutí

    explanation_shown TEXT,                    -- co přesně bylo uživateli vysvětleno před rozhodnutím (audit trail)
    user_comment    TEXT,                      -- volitelná poznámka uživatele

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
-- KEYHOLDER ASSESSMENT (+ trust/reward jako vnořený JSON stav)
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


-- Historie trust_score jako samostatná časová řada — odděleno od
-- keyholder_assessments.trust_state_json, protože chceme snadno dotazovatelný
-- trend v čase bez nutnosti parsovat JSON přes celou historii assessmentů.
CREATE TABLE IF NOT EXISTS trust_history (
    id              TEXT PRIMARY KEY,
    recorded_at     TEXT NOT NULL,
    trust_score     REAL NOT NULL,
    reason          TEXT NOT NULL,              -- lidsky čitelný důvod změny
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

    -- Dvouvrstvé requires_user_approval (viz konverzace): pevná pravidla OR impact score
    is_critical_change      INTEGER NOT NULL DEFAULT 0,  -- 0/1 — critical/rule/safety/philosophy change
    requires_user_approval  INTEGER NOT NULL,   -- 0/1 — výsledné pole: is_critical_change OR impact.is_significant (+ safety override)
    safety_override         INTEGER NOT NULL DEFAULT 0,  -- 0/1 — bezpečnostní vrstva přebila normální vyhodnocení (philosophy.md 2.3)

    explanation              TEXT,               -- generováno jen pokud je rozhodnutí významné
    approval_status           TEXT NOT NULL DEFAULT 'not_required', -- 'not_required' | 'pending' | 'approved' | 'rejected'

    FOREIGN KEY (context_snapshot_id) REFERENCES context_snapshots(id),
    FOREIGN KEY (coach_assessment_id) REFERENCES coach_assessments(id),
    FOREIGN KEY (keyholder_assessment_id) REFERENCES keyholder_assessments(id)
);

CREATE INDEX IF NOT EXISTS idx_decision_context ON decision_results(context_snapshot_id);
CREATE INDEX IF NOT EXISTS idx_decision_approval ON decision_results(approval_status);
CREATE INDEX IF NOT EXISTS idx_decision_created ON decision_results(created_at);


-- =============================================================================
-- OBSERVATIONS (write-only z pohledu runtime, čte jen audit export)
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

    -- audit export tato pole vyplní při zpracování; runtime je nikdy nečte ani nenastavuje
    reviewed_at          TEXT,
    review_notes          TEXT,

    FOREIGN KEY (related_decision_id) REFERENCES decision_results(id)
);

CREATE INDEX IF NOT EXISTS idx_observations_type ON observations(observation_type);
CREATE INDEX IF NOT EXISTS idx_observations_flagged ON observations(flagged_for_review);
CREATE INDEX IF NOT EXISTS idx_observations_created ON observations(created_at);


-- =============================================================================
-- CONVERSATION LOG (krátkodobá paměť / surová historie zpráv)
-- =============================================================================
-- Toto je surový log zpráv pro Discord bota a krátkodobou paměť. Dlouhodobá
-- paměť (extrahované poznatky, vzorce) bude řešena v pattern_engine /
-- memory_engine nad embeddings vrstvou — přidá se v pozdější migraci, až
-- bude jasná konkrétní podoba (Fáze 4).
-- -----------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS conversation_messages (
    id              TEXT PRIMARY KEY,
    created_at      TEXT NOT NULL,
    role            TEXT NOT NULL,          -- 'user' | 'assistant' | 'system'
    content         TEXT NOT NULL,
    discord_channel_id TEXT,
    discord_message_id TEXT,

    -- volitelná vazba na rozhodnutí, pokud zpráva vznikla v souvislosti s ním
    related_decision_id TEXT,

    FOREIGN KEY (related_decision_id) REFERENCES decision_results(id)
);

CREATE INDEX IF NOT EXISTS idx_conversation_created ON conversation_messages(created_at);


-- =============================================================================
-- Seed: zápis této migrace do schema_version
-- =============================================================================
INSERT INTO schema_version (version, applied_at, description)
VALUES (1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'Initial schema: rules, consent_log, context/coach/keyholder/decision, observations, conversation_messages');

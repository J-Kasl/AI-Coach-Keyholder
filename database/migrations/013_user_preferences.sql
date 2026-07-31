-- =============================================================================
-- Migration 013 — Application Layer: user_preferences (Discord onboarding)
-- =============================================================================
-- docs/architecture/user_onboarding_technical_design.md.
--
-- One row per UserAccount (migration 011), tracking onboarding
-- progress and the three approved preferences: language, ai_gender,
-- identity_id. FK to user_accounts is a real one (not a source_ref-
-- style reference) -- unlike a plugin's own table, this lives in the
-- same application layer that already owns user_accounts itself
-- (application/user_service.py), the same relationship
-- user_channel_identities already has to user_accounts.
--
-- Deliberately does NOT reference ai/identity_catalog.py's fifteen
-- identity_ids with a hard FK/CHECK constraint -- validation that a
-- chosen identity_id is real happens in application code
-- (application/onboarding_service.py), the same way every other
-- "is this choice valid" check in this system lives in code, not in
-- a database CHECK constraint.
-- =============================================================================

CREATE TABLE IF NOT EXISTS user_preferences (
    user_id             TEXT PRIMARY KEY REFERENCES user_accounts(id),
    onboarding_step       TEXT NOT NULL DEFAULT 'language',
    language                TEXT,
    ai_gender                 TEXT,
    identity_id                  TEXT,
    created_at                     TEXT NOT NULL,
    updated_at                      TEXT NOT NULL
);


-- =============================================================================
-- Seed: record this migration in schema_version
-- =============================================================================
INSERT INTO schema_version (version, applied_at, description)
VALUES (13, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 'Application layer: user_preferences (Discord onboarding -- language/ai_gender/identity_id)');

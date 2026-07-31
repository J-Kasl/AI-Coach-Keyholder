# User Onboarding / User Preferences — Technical Design (v1.0)

> **Status: Architecture baseline — approved for implementation.**
> Implemented in full — see Section 9.
>
> A deliberately separate document from `ai_identity_technical_design.md`
> (which remains a full draft, not approved for implementation) — not
> because the two are unrelated, but because splitting one document's
> status field into "half approved" would have been less clear than
> two documents with unambiguous, single statuses each. This document
> owns the onboarding flow and the preference data model. It explicitly
> does **not** own, and must never grow into, anything
> `ai_identity_technical_design.md` itself owns (Section 1 states the
> boundary precisely).

## 1. What This Document Approves, and What It Does Not

**Approved for implementation here:**
- The three-step onboarding selection (language → AI gender → personality).
- The data model and persistence for these three preferences.
- Reading `ai_identity_technical_design.md` Section 3 (the 15-identity
  catalog: internal IDs, groups, archetypes, localized display names)
  and Section 10 (the six `CommunicationProfile` values per identity)
  as **approved, stable reference data** for display and validation —
  narrowly, for this purpose, independent of that document's own
  broader draft status for its communication pipeline.

**Explicitly NOT approved here** (unchanged — still governed entirely
by `ai_identity_technical_design.md`'s own draft status):
- Generating or rephrasing any final AI response according to a stored
  identity preference.
- Any connection between a stored identity preference and a `Decision`
  (`relationship_decision_engine_technical_design.md`).
- The Relationship Engine, Decision Engine, or Hidden Token Economy.
- Any way for a stored identity preference to influence a domain
  decision.
- The full AI Identity communication pipeline
  (`ai_identity_technical_design.md` Sections 4–17) in any form.

A stored preference, in this phase, is exactly that: **a preference,
recorded and available for a future, separately-approved communication
layer to read** — not wired to anything yet. `help`, `status`, and every
other existing deterministic reply are unaffected and are not
rephrased according to the stored identity.

## 2. Single Source of Truth for the Identity Catalog

`ai_identity_technical_design.md` Section 3 (names/groups/archetypes/
localization) and Section 10 (the six values) remain the **one**
canonical source for that content. `ai/identity_catalog.py` is a
direct, literal transcription of those two tables into code — if they
ever diverge, the design document is authoritative and the code module
has drifted. This document does not redefine the catalog, and no
second table anywhere claims to be authoritative for "what are the
fifteen identities."

## 3. The Onboarding State Machine

```
LANGUAGE  --valid answer-->  AI_GENDER  --valid answer-->  PERSONALITY  --valid answer-->  COMPLETE
   |                              |                              |
   `-- invalid: re-prompt,        `-- invalid: re-prompt,        `-- invalid: re-prompt,
       no write                       no write                       no write
```

Persisted (`user_preferences.onboarding_step`), never held only in
memory — a process restart resumes correctly because the next incoming
message simply re-reads this same persisted value. There is no
separate "resume" code path; persistence *is* the resume mechanism.

**A user's first-ever message is never itself treated as an answer.**
`OnboardingService.get_or_create_preferences()` reports whether the row
was just created; if so, the caller shows the current step's prompt
directly, without passing that first message's text into
`process_message()` — otherwise a brand-new user's very first reply
from the bot would be a confusing "I didn't recognize that."

## 4. Data Model

```
UserPreferences (one row per UserAccount):
    user_id: str            # PK, FK -> user_accounts.id (application/user_service.py)
    onboarding_step: OnboardingStep  # 'language' | 'ai_gender' | 'personality' | 'complete'
    language: str | None       # 'en' | 'cs' -- None until answered
    ai_gender: str | None       # 'female' | 'male' | 'neutral' -- None until answered
    identity_id: str | None      # one of the 15 catalog IDs -- None until answered
    created_at: datetime
    updated_at: datetime
```

`identity_id` is validated against `ai/identity_catalog.py` in
application code, not a database `CHECK`/foreign key constraint — the
same place every other "is this choice valid" check in this system
already lives.

**Verified under review:** `user_id` is the table's actual `PRIMARY
KEY` (not merely a unique index added separately) — a duplicate
`UserPreferences` row for the same user is structurally impossible at
the schema level, independent of `get_or_create_preferences()`'s own
get-or-create logic; a second `INSERT` for an existing `user_id` would
raise `sqlite3.IntegrityError` before any application code could act
on it. No additional index is needed beyond the primary key's own
implicit one, since every lookup in this module is `WHERE user_id = ?`.
Migration 013 is idempotent the same way every other migration in this
project is — `CREATE TABLE IF NOT EXISTS` is a no-op on a second run,
and re-running its `schema_version` seed `INSERT` would itself fail
cleanly (that table's own `version` column is a primary key) rather
than silently duplicate a version record; in practice this file is
never re-executed at all once applied, since `database/database.py`'s
`migrate()` tracks exactly this via `schema_version` — confirmed
directly (`[1..13]` first run, `[]` second run) alongside this
review, not merely inferred from the pattern other migrations follow.

## 5. Language and Localization Scope

**Onboarding prompt text is English-only in this phase.** No
localization mechanism exists yet anywhere in this codebase to route
`language`-preference-driven prompt text through, and building one is
explicitly out of this document's scope. The one place localization
*is* applied, per the approved scope, is identity **display names**
(`ai/identity_catalog.py`'s `display_name(language)`) — `sophia` →
"Sofie" once `language="cs"` is stored, exactly the approved,
narrow localization rule. `language` itself is stored for a future
localization mechanism to read; nothing reads it for prompt text yet.

## 6. Write-Before-Send

Every step transition writes `user_preferences` in one transaction
*before* any reply is generated for sending. The Discord adapter
(`bot/discord_bot.py`) sends only after `ApplicationService.handle_message()`
already returned — if the send itself then fails (network/API error),
the persisted state is already correct; the user's next message simply
sees the actual current step, self-healing without special recovery
logic. `bot/discord_bot.py`'s own `channel.send()` call is wrapped in
a try/except for exactly this reason (a real, small hardening made
alongside this feature — see its own comment).

**A verified, known limitation of this choice, found under direct
review (not hypothetical):** if a send genuinely fails after a
successful write, and the user — never having seen a reply — resends
the *same* answer, the second message is evaluated against the *new*
current step, not the one they think they're still answering.
Concretely: answer "english" for LANGUAGE succeeds (write completes,
send fails); resending "english" lands on AI_GENDER, which does not
recognize "english" as a valid answer, producing "I didn't recognize
that" followed by the AI_GENDER prompt. **State is never corrupted and
no progress is lost** — `language` stays `"en"`, the step stays
`AI_GENDER` — but the message can read as confusing rather than as
"you already answered that." Reproduced directly, not assumed:
`OnboardingService.get_or_create_preferences()` followed by two
`process_message()` calls, the second against a freshly re-read
current row (matching exactly what `ApplicationService.handle_message()`
does for every incoming message). Judged acceptable for this phase —
the user only has to notice the next prompt is a *different* question
and answer that instead, not lose or repeat any actual progress — but
recorded here explicitly rather than glossed over, since a smoother
recovery (e.g. detecting "this answer was already given" and saying so
outright) is a real, legitimate future improvement, not implemented
here to avoid scope creep on a working, already-safe mechanism.

## 7. Duplicate/Stale Message Safety

Every transition is an atomic, conditional `UPDATE ... WHERE
onboarding_step = <the step the caller believes is current>`. If the
affected row count is 0, something else (most plausibly a duplicated
Discord dispatch delivering the same message twice) already advanced
this user past that step — the actual current row is re-read and
returned instead of forcing the stale write through. No message-ID-based
deduplication exists at the Discord-adapter level for this phase; this
conditional-update pattern is the actual safety net, and is
demonstrated directly by `tests/application/test_onboarding_service.py::TestDuplicateMessages`
— including, under direct review, a genuine multi-threaded concurrency
test (`test_two_truly_concurrent_advances_never_double_advance_or_corrupt_state`),
not only a sequential simulation: two threads, each with their own
`OnboardingService`/`sqlite3` connection (`infrastructure/database.py`
opens a fresh connection per `.transaction()` call — confirmed
directly, not assumed), racing via a `threading.Barrier` to advance
the same user from the same step at genuinely the same instant. Run
repeatedly (8 consecutive passes checked under review), never a double
advance, never state corruption — SQLite's own file-level locking
(this project's configured `busy_timeout` makes a second writer wait
rather than error) combined with the single-statement conditional
`UPDATE` is what actually guarantees this, not application-level
locking this project does not have.

## 8. Ownership / Ownership Boundary

| Concern | Owner |
|---|---|
| Onboarding flow, state machine, `user_preferences` table | This document |
| The 15-identity catalog's content (names, groups, archetypes, localization, six values) | `ai_identity_technical_design.md` Sections 3/10 (draft status for the rest of that document does not extend to these two specific, static tables) |
| Any future use of a stored preference to phrase a message | `ai_identity_technical_design.md` (remains unapproved) |
| `Decision`, Relationship/Decision Engine | `relationship_decision_engine_technical_design.md` (remains unapproved) |

## 9. Implementation

Done, in full, as of this document's v1.0:

- `ai/identity_catalog.py` — the catalog transcription (Section 2).
- `database/migrations/013_user_preferences.sql`.
- `application/models.py` — `OnboardingStep`, `UserPreferences`.
- `application/onboarding_service.py` — the state machine.
- `application/service.py` — `ApplicationService.handle_message()` now
  checks onboarding status before routing to the command table; a new
  read-only `preferences` command shows a completed user's saved
  choices.
- `bot/discord_bot.py` — `channel.send()` wrapped defensively
  (Section 6).
- Tests: `tests/application/test_onboarding_service.py`,
  `tests/application/test_service.py` (updated), `tests/bot/test_discord_bot.py`
  (updated + new).

## 10. Deliberately Deferred

- Any communication-layer rephrasing of AI replies by identity — the
  entirety of `ai_identity_technical_design.md`'s own pipeline.
- Full "change my personality" re-onboarding flow — only a read-only
  `preferences` command exists.
- Actual multi-language prompt rendering (Section 5).
- Message-ID-based Discord dedup — the conditional-update pattern
  (Section 7) is judged sufficient for this phase.
- Embeds/rich UI — plain text throughout; nothing about DM permissions
  makes an embed necessary here.

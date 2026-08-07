# Preference & Limits Profile

Canonical design: `docs/architecture/preference_limits_profile_technical_design.md`
(**`Draft for review, not approved for implementation`** — this README
describes exactly which specific slice of that draft has been
implemented here, and nothing more).

## What is implemented here — Foundation Slice 1 only

A pure, process-independent domain model. **No repository, no revision
workflow, no import proposals, no consent, no eligibility integration,
no external providers, and no runtime wiring of any kind** — this
package is not called from anywhere else in the project, and it does
not call anywhere else either (verified by AST scan,
`tests/preference_profile/test_system_independence.py`).

**`models.py`**:
- `ProfileOwnerKey` — an opaque owner identifier. This domain never
  imports `UserAccount`, Discord, or database types; a future
  composition/application layer may derive this value from
  `UserAccount.id`, but this module has no knowledge of that.
- `ProfileTopicId` — `namespace` (a stable *taxonomy family*
  identifier, e.g. `"provider_neutral"` — never a version number like
  `"provider_neutral_v1"`, which would silently conflate a family
  identifier with future versioning) plus `value`. Equality is
  structural and case-sensitive; no normalization beyond emptiness
  validation; ordering is not defined.
- `ProfileDisposition` — `PREFERENCE` / `SOFT_LIMIT` / `HARD_LIMIT`.
- `ProfileEntry` — `id`, `owner_key`, `topic`, `disposition`. **No**
  `confirmation_status`, `confirmed_at`, `supersedes_entry_id`,
  revision number, source/provider metadata, or consent metadata.
  Existence of an entry in a `PreferenceProfileSnapshot` **is** the
  confirmed, active state — there is no other state this type can
  represent.
- `PreferenceProfileSnapshot` — at most one active entry per
  `(owner_key, topic)`, enforced constructionally in `__post_init__`
  (Cardinality Variant A), not merely documented. Also rejects any
  entry belonging to a different owner. Error messages never contain
  the owner key or topic identifier — both may themselves be
  sensitive; messages stay generic (e.g. "PreferenceProfileSnapshot
  cannot contain duplicate active topics.").
- `TopicState` — `HARD_LIMIT` / `SOFT_LIMIT` / `PREFERENCE` /
  `NO_ACTIVE_STATEMENT`.

**`policy.py`**: `resolve_topic_state(*, snapshot, topic) -> TopicState`
— pure, deterministic, no side effects, no logging, no DB access, no
import of any other subsystem. Because a snapshot can never contain
more than one active entry per topic, this function never performs
conflict resolution or precedence over multiple values — it only
finds the single active entry (if any) and maps its disposition to a
`TopicState`.

The business precedence rule —

> hard limit > soft limit > no active statement > preference

— remains a **documented rule for a future update policy and a future
eligibility policy** (neither exists in this slice), not an algorithm
this function implements, since there is never more than one active
value to compare here.

## What is explicitly NOT implemented — still draft, still open

- **No repository, no persistence, no migration.** This slice is pure
  in-memory Python types — no `sqlite3`, no `Database`, no transaction.
- **No `ExternalSourceId`, `ImportProposal`, `ImportProposalItem`, or
  `UnresolvedMappingToken`.** Provider-neutral import/review workflow
  is a future Slice 2 (or a separate import-foundation slice) concern.
- **No `ConsentRecord`/`ConsentPurpose`.** A future Slice 5 concern,
  which will need to define explicit consent *scope*
  (provider-level/proposal-level/entry-level/processing-purpose) —
  not decided here.
- **No `TaskEligibilityDecision` or any eligibility type.** A future
  Slice 3 (`limits_policy`) concern.
- **No age/eligibility gate, no `EligibilityGate`-style Protocol.**
  Deliberately not created here — defining an interface for a
  capability that should exist project-wide, before that capability
  has its own separate design, risks this one sensitive module quietly
  shaping a mechanism meant to apply more broadly. **Any user-facing,
  application, bot, Task Catalog, or Conversation Engine integration
  of this package is fail-closed blocked** until a separately approved
  age/eligibility design exists. No composition root anywhere in this
  project constructs a user-accessible instance of this subsystem.
- **No `external_profile_import/` or `limits_policy/` packages.**
  Deferred to their own future slices, per YAGNI — Slice 1 does not
  speculatively create packages whose real responsibilities aren't
  needed yet.

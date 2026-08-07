# Preference & Limits Profile — Architectural Proposal (v1.0)

> **Status: Draft for review, not approved for implementation.** This
> document describes a subsystem for storing a normalized,
> user-confirmed profile of preferences, interests, and limits — later
> intended to filter/rank Task Catalog templates, safely bound
> Conversation Engine's own suggestions, and support future import
> from external sources (used here only as generic examples of an
> external questionnaire/profile source, not as an approved
> integration target). Nothing in this document is built until it, or
> the specific slice it describes, is separately approved — the same
> discipline `advanced_mode_technical_design.md` and
> `task_catalog_technical_design.md` already established before their
> own first implementation slices.

## 1. The Question This Document Answers

Where does a user's confirmed preferences and limits live, who owns
that data, and what precedence governs a conflict between "I like
this" and "I cannot do this" — answered once, centrally, rather than
re-derived ad hoc by every future consumer (Task Catalog eligibility,
Conversation Engine's own restricted context).

## 2. Foundation Slice 1 — Implemented

> See `preference_profile/README.md` for the exact, currently-true
> boundary. Summarized here for document completeness.

A pure, process-independent domain model: `ProfileOwnerKey`,
`ProfileTopicId`, `ProfileDisposition`, `ProfileEntry`,
`PreferenceProfileSnapshot`, `TopicState`, `resolve_topic_state()`.
No repository, no persistence, no import, no consent, no eligibility
integration, no runtime wiring anywhere in this project.

### 2.1 Cardinality — Variant A

At most one active entry per `(owner_key, topic)`, enforced in
`PreferenceProfileSnapshot.__post_init__`. A new confirmed entry
always supersedes the previous one (superseding itself is a future
Slice 2 concern — this slice's own snapshot type has no history/revision
field at all). Consequently, precedence between hard/soft/preference
for one topic is never a question of *reading* a snapshot (there is
always at most one value); it is a question of a *future update
policy* deciding whether a proposed change may replace an existing
active entry — out of this slice's scope entirely.

### 2.2 `ProfileEntry` — No Confirmation Status

Deliberately has no `confirmation_status`, `confirmed_at`,
`supersedes_entry_id`, revision number, source/provider metadata, or
consent metadata. An entry's mere presence in a
`PreferenceProfileSnapshot` **is** the confirmed, active state — there
is no other state this type can represent, and no invalid state
(e.g. "unconfirmed entry sitting in an active snapshot") is
constructible.

### 2.3 `resolve_topic_state()`

Pure, deterministic, no side effects. Because at most one entry can
exist per topic, this function performs no conflict resolution --
only a lookup and a disposition-to-state mapping. Business precedence
(`hard limit > soft limit > no active statement > preference`) is
documented for future consumers (a future update policy, a future
eligibility policy), not implemented as an algorithm here.

## 3. Future Design — Not Implemented, Sketched for Continuity Only

The sections below describe direction, not commitments. Every type
name, field, and Protocol here is illustrative -- none of it is
approved, and each future slice will define its own actual contract
independently, informed by this sketch but not bound by it.

### 3.1 Import/Review Workflow (future Slice 2 or a separate import-foundation slice)

```
provider payload (stays inside a provider-specific adapter, never
crosses into provider-neutral code)
        v provider-specific parser
provider-neutral normalized candidate (topic candidate or a safe
unresolved-mapping marker, proposed disposition, mapping/confidence
status -- never raw free text, never a provider-specific name)
        v validation, conflict detection
ImportProposal (staging/review object -- explicitly separate from
PreferenceProfileSnapshot; never itself authoritative)
        v explicit user confirm() (per item)
new PreferenceProfileSnapshot revision
```

**Governing rule**: *external import may propose; only the user or an
explicit internal policy may confirm.* An import can never silently
alter an existing active `ProfileEntry`. Repeated identical imports
must be idempotent; a provider "removing" an item never revokes an
already-confirmed entry.

An unresolved/free-text candidate's own storage strategy remains
undecided -- a raw-payload back-reference that could outlive the
adapter call that produced it is explicitly rejected as unsafe. Real
candidates for a future slice: review happens within the same
short-lived session as the import itself (no persistence of the
unresolved item at all), or only a provider item identifier plus a
pre-normalized safe display label is stored -- never a raw-text
reference to a payload that might no longer exist.

### 3.2 Consent (future Slice 5, after privacy/consent approval)

A single consent record with one generic `purpose` field is
insufficient -- a future design must distinguish, at minimum:
provider-connection authorization, one-time import consent, recurring
sync consent, per-entry confirmation, consent to use the confirmed
profile for task eligibility, and consent to use it for Conversation
Engine's own restricted context. Each needs its own explicit scope;
none of this is decided yet.

### 3.3 Task Eligibility (future Slice 3, `limits_policy`)

```
TaskEligibilityPolicy.evaluate(*, profile_snapshot, task_requirements) -> TaskEligibilityDecision
```

`eligibility first, ranking second` -- preference may only influence
ranking among templates that already passed eligibility. A decision's
publicly loggable surface must never carry raw topic identifiers or
other data that could itself be sensitive -- a topic ID is not
automatically safe to log just because it is "only an ID." `limits_policy`
defines its own input types (e.g. `TaskPreferenceRequirements`), never
importing `task_catalog.models.TaskTemplateVersion` directly into this
domain.

### 3.4 Conversation Engine Integration (future Slice 4)

Not automatically a `ConversationContextProvider` -- that boundary
choice is this future slice's own decision, made on its own merits
(least privilege, whether the engine needs precise call timing the
way Working Memory does), not copied from either Working Memory's own
direct-DI pattern or an assumed provider pattern. Raw preference data
must never reach a prompt. A purpose-limited policy decision -- not a
general "what the user allows" snapshot -- is the safer default
direction; whether Conversation Engine needs a profile snapshot at
all, versus only a pre-computed policy decision, remains open.

### 3.5 Age/Eligibility Boundary

No gate Protocol exists in this package, and none is added here --
defining an interface for a capability that should exist project-wide
before that capability has its own separate, approved design risks
this one sensitive module quietly shaping a mechanism meant to apply
more broadly. Until a separately approved age/eligibility design
exists: no composition root anywhere constructs a user-accessible
instance of this subsystem; no application, bot, Task Catalog, or
Conversation Engine integration is permitted. This is fail-closed by
the simple fact that no such wiring exists -- not by a technical gate
inside this package.

## 4. Explicitly Blocked

Any integration connecting this package to a real user (application
layer, bot commands, Task Catalog runtime, Conversation Engine
runtime) -- blocked until a separately approved age/eligibility design
exists. Persistent storage of any preference/limit data -- blocked
until a separately approved privacy/consent design exists (this
project's own current consent mechanism is an audit-trail string,
never a verified informed-consent record; no account-deletion or
data-export mechanism exists anywhere in this project today).

## 5. Not Yet Decided

- Exact future `ImportProposal`/`ImportProposalItem` field shapes and
  their concurrency contract (proposal revision + profile revision,
  likely, per this project's own established optimistic-concurrency
  convention -- not finalized).
- Exact consent scope taxonomy (Section 3.2).
- Whether Conversation Engine needs a profile snapshot at all, or only
  a pre-computed decision (Section 3.4).
- Taxonomy namespace governance beyond "namespace is a stable family
  identifier, not a version" -- who owns adding a new namespace, how a
  topic split/merge is represented, is not decided.

## 6. Implementation Roadmap

1. **Foundation Slice 1** (this document's own Section 2) -- done.
2. **Slice 2** -- in-memory profile repository, revision/supersession
   workflow, explicit confirmation flow. Still no DB, no external
   providers, no user-facing wiring.
3. **Slice 3** -- Task eligibility integration (`limits_policy`).
4. **Slice 4** -- restricted Conversation Context integration.
5. **Slice 5** -- persistence and consent, only after a separate
   privacy/consent design is approved.
6. **Slice 6+** -- provider feasibility and adapters, one at a time,
   each independently audited (does a real, usable API exist; auth;
   scopes; rate limits; terms of service; data deletion; token
   revocation) before any adapter code is written.

# Lock State — Architectural Proposal (v1.0)

> **Status: Draft for review, not approved for implementation.** This
> document describes a subsystem for tracking whether the user has
> reported their keys/lock as secured — the first small piece of the
> broader keyholding domain this project intends to build toward,
> eventually including an external provider integration (used here
> only as a generic example, not an approved integration target).
> Nothing beyond what `lock_state/README.md` describes as implemented
> is built until it, or the next slice, is separately approved.

## 1. The Question This Document Answers

What does the system actually know about whether the user is locked,
and how is that knowledge represented so it can never be mistaken for
something the system verified itself.

## 2. The Epistemic Boundary — Non-Negotiable

Without external hardware or a provider integration, this system
cannot know whether physical keys are actually secured. Every status
this domain can represent is what the **user told the system**, never
a verified physical fact. This applies to every future extension of
this domain, not only the slice implemented today:

- A future provider integration would add **provider-observed** state
  -- a genuinely different kind of information than a user report (the
  provider's own sensors/session state, not the user's own words), but
  **still not a guarantee of physical reality** the way, for instance,
  a cryptographic proof would be. It must never be silently presented
  as stronger evidence than it actually is, and it must never
  overwrite or be conflated with the user-reported state without an
  explicit, separately designed reconciliation policy.
- No status anywhere in this domain may be named or interpreted as
  `VERIFIED_LOCKED`, `KEYS_IN_LOCKBOX`, `PHYSICALLY_SECURED`, or
  anything else implying technical verification of physical reality.

## 3. Foundation Slice — Implemented

> See `lock_state/README.md` for the exact, currently-true boundary.
> Summarized here for document completeness.

`LockReportStatus` (two members, both persisted, both explicitly
`_USER_REPORTED`), `LockKnowledgeState` (three members -- the
read-result type, adding `UNKNOWN` as the absence state, never
persisted as a fake row), `LockReport` (immutable), `LockState`
(read-only), `LockStateAdministration` (governed write,
`report_status()`). Append-only persistence (migration 019) -- every
report a new row, current state read as the most recent by an
explicit `sequence_number` tiebreaker, never mutated in place.

No Discord commands, no `ApplicationService` integration, no
`ConversationContextProvider`, no Conversation Engine wiring -- nothing
new is user-accessible through Discord after this slice.

## 4. Future Design — Not Implemented, Sketched for Continuity Only

### 4.1 Conversation Context Integration

A future slice would expose `LockKnowledgeState` to Conversation
Engine through a new `ConversationContextProvider` -- read-only,
optional (a provider failure degrades to no lock context, not a
fabricated "unknown" fragment misrepresented as a hard fact), and
carrying only the three-value state, never a raw report history or
any provider-specific data.

### 4.2 External Provider Integration

A future, separate slice. The relationship between a user report and
a provider-observed state is an explicit design question, not decided
here -- candidates include treating them as two independent read
surfaces (never merged into one row), or a documented reconciliation
policy that still preserves the user-reported value's own history.
`ConversationModel`/the Ollama adapter must never call a provider
directly -- any such integration lives entirely inside a dedicated
adapter, never inside the conversation/generation path.

## 5. Explicitly Blocked

Any Discord/application/Conversation Engine integration -- until a
separately approved slice builds it. Any external/hardware
verification -- no such mechanism exists or is planned to exist
without its own explicit design and approval.

## 6. Implementation Roadmap

1. **Foundation Slice** (this document's own Section 3) -- done.
2. **Conversation Context integration** -- a `ConversationContextProvider`
   exposing `LockKnowledgeState`, read-only.
3. **External provider integration** -- its own multi-step roadmap
   (API/auth survey, read-only sync, canonical mapping, governed writes,
   real control operations), entirely separate from this document's own
   user-report domain.

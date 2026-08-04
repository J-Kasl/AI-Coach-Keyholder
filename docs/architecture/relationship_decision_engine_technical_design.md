# Relationship Engine / Decision Engine — Architectural Proposal (v1.1)

> **Status: Draft architectural proposal — NOT approved for
> implementation.** Answers responsibility and boundary questions only.
> Deliberately excludes: database schema, the Hidden Token Economy's
> internal algorithm, prompt engineering, and any other implementation
> detail. Those are all separate documents, to be written only after
> this one is approved.
>
> **v1.1:** resolved this document's original Open Question 1 (trigger
> timing) into Section 2.1, on review that it shapes almost everything
> else about the pipeline's design. The remaining open questions
> (`RelationshipContext` persistence chief among them) were left open
> deliberately — resolving one open question does not create pressure
> to resolve the rest before they're ready.
>
> Depends on `philosophy.md` v1.16 (Section 4.2, the Hidden Token
> Economy; Section 2.6, transparency of reasons vs. transparency
> of computation) and on four existing, implemented domain modules
> (`trust_manager`, `penalty_engine`, `recovery_plan`,
> `goal_management`), whose narrow public read APIs are this document's
> only source of "Domain State." Supersedes, in intent,
> `database/models.py`'s Phase 0 `CoachAssessment`/`KeyholderAssessment`/
> `DecisionResult`/`TrustState`/`RewardState`/`ImpactScore` — that
> scaffolding was never wired into working code and should not shape
> this design; see this document's own closing note.
>
> The `ai_identity_technical_design.md` document (not yet written)
> depends on this one: it describes only how a selected identity
> phrases a `Decision` this document defines, and requires this
> document's vocabulary to exist first.

## 1. The Question This Document Answers

Four real domain modules exist today, each answering a narrow question
about what happened or what currently is (`penalty_window_technical_design.md`
Section 1's own framing generalizes here): did an Incident occur, is a
Penalty Window active, what has a Recovery Task earned, is a Goal being
met. None of them decides what the system should communicate to the
user right now, or why. Nothing today turns "here is what is true"
into "here is what I'm going to say and do about it, in one voice."

This document answers: **where does that turning-point live, what does
it own, and what does it explicitly not do.**

## 2. The Pipeline

```
Domain State                  (Trust Manager, Penalty Engine,
  |                            Recovery Plan, Goal Management --
  |                            read via each module's own narrow
  |                            public API; no new object invented
  |                            to carry this — see Section 3)
  v
Relationship Engine            interprets Domain State through two
  |  Coach Perspective          structured lenses; produces a
  |  Keyholder Perspective      RelationshipContext (Section 4)
  v
Decision Engine                 classifies the situation's Entitlement
  |  Entitlement Classification  Class (Section 6), consults the
  |  Hidden Token Economy        Hidden Token Economy where the class
  |  (Discretionary/Guaranteed   permits it, and produces exactly one
  |   only)                      Decision (Section 5)
  v
Conversation Engine                selects/receives the active AI
  |  (GOVERNANCE_EXPLANATION       Identity and phrases the Decision's
  |   category, selected AI        explanation in that identity's voice
  |   Identity)
  v
User
```

Conversation Engine (its `GOVERNANCE_EXPLANATION` category) and AI
Identity are `conversation_engine_technical_design.md`'s and
`ai_identity_technical_design.md`'s own subject, not this document's
— included above only to show where this
document's output (`Decision`) goes next.

### 2.1 When the Pipeline Runs

Resolved here from this document's original Open Question 1 (Section
9), on the reasoning that trigger timing shapes almost everything else
about the pipeline's design, unlike Open Question 2 (`RelationshipContext`
persistence), which does not need to be settled before implementation
can start and stays open below.

Three genuinely different trigger categories exist — not one blended
answer, and not "every message" or "only on state change" alone:

**A. Synchronous, request-driven.** A specific request that requires an
answer before a reply can be sent at all: an activity authorization
request (Guaranteed class, Section 6), or — once built — an
open-ended conversational ask that isn't already fully served by an
existing fixed, deterministic command. `application/service.py`'s
`CommandRouter` already demonstrates the boundary this depends on:
`status` today never reaches anything like this pipeline, because a
deterministic domain read already fully answers it. The same
discipline should hold once this pipeline exists — a message that an
existing deterministic path can already answer should not be routed
through Relationship Engine / Decision Engine just because the
pipeline exists.

**B. Event-driven, reacting to a curated subset of domain events.**
Reuses the transactional outbox and consumer pattern already built
(`infrastructure/consumer_registry.py`, wired in `system/startup.py`)
exactly as it exists today — not a new mechanism. The Relationship
Engine / Decision Engine would register as a consumer for specific
event types already judged decision-relevant, the same way Penalty
Engine registers for `incident.confirmation_changed` today (filtered
to `new_confirmation=confirmed` — the same Finding-1 payload-filtering
pattern would apply here too, for whichever event fields matter).
**Not every event type this system publishes is decision-relevant** —
most (`trust_evidence.recorded`, `freeze_periods.opened`,
`extension.decision_recorded`, ...) are internal bookkeeping a domain
module needs for its own consistency, not something that, by itself,
warrants a fresh relationship-level interpretation. Candidates that
plausibly do: `incident.confirmation_changed` (confirmed),
`penalty_window.completed`, `goal.abandoned`, `goal.completed`,
`recovery_plan.task_completed`. The exact, final list is an
implementation-time decision for the follow-up technical design, not
enumerated exhaustively here — deciding it is exactly the kind of
judgment call Trust Manager already makes today about which of its own
events matter enough to reach Penalty Engine.

**C. Scheduled/proactive.** A check-in cadence, independent of any
message or event, driven purely by time passing — the system reaching
out, not only reacting. Genuinely real (implied throughout
`philosophy.md`'s framing of an ongoing coaching relationship) but
**not resolved by this document**, for a concrete reason rather than
an oversight: nothing in this system can trigger it yet.
`core/config.py`'s `quiet_hours_start`/`quiet_hours_end` are explicitly
commented "for a future scheduler (Phase 5)," and — independently —
`trust_manager`'s own `scheduled_review` recalculation trigger is
already deferred for the identical reason
(`trust_manager/README.md`: "`window_completion`/`scheduled_review`
will genuinely need to run in their own later transaction," pending a
check-in/scheduling mechanism that does not exist). This is now the
*third* place in this system independently arriving at the same
missing piece — worth noting as a signal that a scheduler is a real,
recurring need, not a one-off. It becomes buildable only once Phase
5's scheduler exists, at which point this trigger should reuse
whatever that scheduler provides rather than inventing its own.

**PIPE-1:** The pipeline runs synchronously (A) for any request
genuinely requiring an answer before a reply can be sent, and
asynchronously (B) as a registered consumer of a curated, explicitly
enumerated (at implementation time) subset of domain events — never as
a blanket subscription to every event type this system publishes, and
never on every incoming message regardless of whether an existing
deterministic path already answers it fully.

## 3. Where Domain Modules End and Interpretation Begins

A domain module answers **what is true**: Trust Manager's
`get_domain_state()` returns a score and confidence, not a judgment
about whether that score is worrying. Penalty Engine's
`get_active_or_frozen_penalty_window()` returns a window's raw state,
not whether continuing to hold it is still proportionate. This is the
same boundary `implementation_conventions.md` Section 3 (the
Interpretation Handoff Pattern) already establishes between domain
modules themselves, extended one layer further up: **the Relationship
Engine is the first place in this system that asks what a fact means
for the relationship, rather than only what the fact is.**

**No new "Domain State snapshot" object is introduced by this
document.** The temptation to invent one (a single struct bundling
everything every domain module currently exposes) is deliberately
resisted — each existing narrow read API
(`TrustManager.get_domain_state()`, `PenaltyEngine.get_active_or_frozen_penalty_window()`,
`RecoveryPlanManager.get_recovery_plan_for_window()`,
`GoalManager.get_goal()`, and others already built) remains the way the
Relationship Engine reads what it needs, exactly as any other consumer
in this system does. A bundling object, if one turns out to be
genuinely useful, is an implementation-time decision for the (not yet
written) Relationship Engine technical design that follows this one —
not invented here speculatively.

**REL-1:** The Relationship Engine never writes to any domain module's
tables, and calls only each domain module's existing public read API —
the same discipline every consumer in this system already follows.

## 4. The Relationship Engine

### 4.1 Responsibility

Produces a **`RelationshipContext`**: a small, structured interpretation
of current Domain State, computed from two perspectives that are always
computed together, from the same read of the same state, at the same
point in time.

- **Coach Perspective** — interprets state through a growth/support
  lens: momentum, whether the person appears to be struggling or
  thriving right now, whether a Goal's target still fits, whether
  encouragement or a check-in is called for.
- **Keyholder Perspective** — interprets state through a
  consistency/accountability lens: whether Rules are being honored,
  which direction Trust is trending, whether a boundary needs holding
  firm right now.

Both perspectives read the *same* Domain State. Neither reads the
other's output, negotiates with the other, or is aware of the other's
existence as a separate process — they are two lenses applied once,
not two agents in dialogue. `philosophy.md`'s existing "Dual
Perspective Architecture" language describes exactly this relationship,
predating this document; this document is that language's first formal
technical treatment.

**REL-2:** A `RelationshipContext` is never shown to the user directly,
in whole or in part. It exists only to be consumed by the Decision
Engine (Section 5).

**REL-3:** Coach Perspective and Keyholder Perspective are each
produced from one shared read of Domain State — never two separate
reads that could observe different states, and never a process where
one perspective's output can change before the other's is computed.

### 4.2 What a Perspective Is (and Is Not)

A perspective is an **interpretation**, not a recommendation and not a
decision. "Trust in the fitness domain has been declining for three
recalculations" is a fact (Domain State). "This looks like a pattern
worth addressing, not an isolated dip" is an interpretation (Keyholder
Perspective). "Extend the Penalty Window by six hours" is a decision —
and belongs exclusively to the Decision Engine, never to a perspective.
No perspective produces, or has access to invoke, any domain module's
write API.

## 5. The Decision Engine

### 5.1 Responsibility

Takes exactly one `RelationshipContext` and produces exactly one
**`Decision`** — the system's single, unified answer to "what happens
now, and why." A `Decision` always carries:

- its Entitlement Class (Section 6),
- the outcome, if any (which may be "no action" — most Decisions,
  most of the time, likely resolve to nothing needing to happen),
- a human-understandable `explanation` — the *only* thing about a
  Decision's reasoning ever exposed outside the Decision Engine.

**DEC-1:** Exactly one `Decision` per decision-worthy situation — never
two, never a Coach answer and a Keyholder answer sent separately (this
is the concrete mechanism preventing what point 2 of the approved
product decisions calls "two independent agents").

**DEC-2:** Every `Decision` has a non-empty `explanation`, regardless
of entitlement class or outcome — including a "no action needed"
outcome. This is the same discipline `ExtensionDecision` and
`RecoveryCreditDecision` already practice (both always write a
non-empty `explanation`, even when the outcome is zero effect); the
Decision Engine is expected to be the point where this becomes a
system-wide rule, not a per-module habit.

### 5.2 Applying a Decision's Effect

When a `Decision`'s outcome requires a real change (extending a Penalty
Window, granting a partner unlock, crediting recovery hours), the
Decision Engine applies it by calling the **owning domain module's own
existing public write API** — the exact same interface any other
caller uses (e.g. `PenaltyEngine`'s own methods). The Decision Engine
never writes directly into another module's tables and never bypasses
that module's own invariants to apply an effect faster.

**DEC-6:** A `Decision`'s effect, if any, is always applied through the
owning domain module's own public API — never a direct write, never a
`_*_in_transaction` method reached into from outside.

### 5.3 The Hidden Token Economy

Owned exclusively by the Decision Engine. No other layer — not the
Relationship Engine, not any domain module, not Conversation Engine
— reads or writes it. It is consulted only where the Entitlement
Class of the current situation permits (Section 6) — never for
Absolute-class decisions, which never touch it.

**DEC-4:** Absolute-class decisions never consult the Hidden Token
Economy — their outcome does not depend on it in any way.

**DEC-5:** The Hidden Token Economy's raw state — any value, weight,
computation, or intermediate number — is never included in a
`Decision`'s `explanation`, never exposed through any read API outside
the Decision Engine itself, and never logged anywhere a user-facing
surface could reach. This is `philosophy.md` 4.2's rule, restated as an
architectural invariant rather than only a communication-style
preference: the boundary is enforced by what data structures exist and
who can read them, not only by an instruction to the identity layer to
not mention numbers.

### 5.4 One Unified Decision, Not a Negotiation

The Decision Engine does not run a negotiation between Coach and
Keyholder perspectives, and does not pick a "winner." It reads both
perspectives (already interpretations, not competing proposals) as
input to one deterministic-where-possible process (Section 8) that
produces one answer. Where the two perspectives point in different
directions — e.g. Coach Perspective suggests easing off, Keyholder
Perspective suggests holding firm — that tension is exactly the kind
of nuance a `Decision`'s `explanation` is expected to surface honestly
("I want to give you room here, but we've drifted from the agreed plan
twice this week, so I'm holding the current course a little longer") —
never resolved by hiding one side, per `philosophy.md` 2.6's existing
"instead of hiding them" language (currently written about the older
Phase 0 shape; the intent transfers directly to this one).

## 6. Entitlement Classes

Every decision-worthy situation is classified into exactly one of three
classes **before** anything else about it is decided — including
before the Hidden Token Economy is consulted at all.

- **Absolute** — the outcome is fixed regardless of any other input.
  Matches `philosophy.md` 4.2's Prohibited Categories exactly: "never
  unlocked by favorable internal standing... or any runtime exception."
  Deterministic by definition; never reaches the Hidden Token Economy,
  never reaches any future LLM-assisted reasoning.
- **Guaranteed** — the outcome follows a fixed, pre-agreed rule applied
  to current state. `activity_authorization_technical_design.md`'s
  existing `authorize_activity()` (a pure function of request,
  definition, policy, and balance) is already exactly this shape,
  already implemented as architecture (not yet as running code) — this
  document does not change that function, only names the category it
  already belongs to. The Hidden Token Economy may be one input the
  fixed rule reads (e.g. a balance threshold), but the *rule itself* is
  not a judgment call the Decision Engine is free to vary.
- **Discretionary** — a genuine judgment call. The only class where
  the Relationship Engine's two perspectives materially shape the
  outcome, and the only class where future LLM-assisted reasoning is
  expected to eventually participate (Section 8).

**DEC-3:** Entitlement classification happens first, deterministically,
independent of the Hidden Token Economy's own state — a situation's
class is never itself a discretionary judgment.

**Point 8 of the approved product decisions** ("Partner Unlocks are
Guaranteed, not Discretionary") is a direct instance of this
classification, not a special case requiring its own mechanism —
Partner Unlocks are Guaranteed the same way any other
`authorize_activity()`-governed request is.

## 7. What Passes Between Layers

| From | To | Object | Ever shown to the user? |
|---|---|---|---|
| Domain modules | Relationship Engine | (no new object — each module's own existing read API) | No (not applicable) |
| Relationship Engine | Decision Engine | `RelationshipContext` (Coach Perspective + Keyholder Perspective) | Never (REL-2) |
| Decision Engine | Conversation Engine (`GOVERNANCE_EXPLANATION` category) | `Decision` (entitlement class, outcome, `explanation`) | The `explanation`, always. The entitlement class and raw outcome structure: implementation question for `ai_identity_technical_design.md`, not decided here. |

**DEC-7:** Conversation Engine (its `GOVERNANCE_EXPLANATION` category, per
`conversation_engine_technical_design.md`) receives only the `Decision` object.
It has no read access to any domain module, to `RelationshipContext`,
or to the Hidden Token Economy — the architectural guarantee behind
point 2's "identity cannot affect any mechanical decision": it cannot
affect what it cannot see.

## 8. Deterministic Today, LLM-Assisted Later

| Layer | Today | Later |
|---|---|---|
| Domain modules | Fully deterministic (already true, verified across all four) | Stays fully deterministic — out of scope for LLM involvement entirely |
| Relationship Engine | Rule-based interpretation of structured Domain State | The most likely first home for LLM-assisted nuance (e.g. reading qualitative signal from a conversation) — additive to, never a replacement for, the deterministic core |
| Decision Engine — Entitlement classification | Deterministic, always | **Stays deterministic, always** — this is a safety invariant (DEC-3), not a current-state limitation to be lifted later |
| Decision Engine — Absolute / Guaranteed | Deterministic, always | **Stays deterministic, always** — same reasoning as `authorize_activity()`'s existing design |
| Decision Engine — Discretionary | Not yet built | The other candidate for LLM-assisted reasoning, bounded by DEC-2 (still always produces a real `explanation`) and DEC-5 (still never reveals Hidden Token Economy internals) |
| Conversation Engine (`GOVERNANCE_EXPLANATION` category) | Not yet built | The primary home for LLM usage: phrasing a `Decision`'s already-fixed `explanation` in the selected identity's voice — the LLM's job is phrasing, never inventing or altering the reason itself |

## 9. Open Questions Before Implementation

Deliberately unresolved here, for the follow-up technical design
document to answer with actual implementation detail. Trigger timing
(originally listed first here) is now resolved in Section 2.1 — it was
addressed ahead of the others specifically because it shapes almost
everything else about the pipeline's design, unlike the questions
below, none of which need to be settled before implementation can
start:

1. **Is `RelationshipContext` persisted?** — every other
   Assessment-shaped object in this system (`ExtensionDecision`,
   `RecoveryCreditDecision`, `GoalEvaluation`) is append-only and
   persisted for audit. Whether `RelationshipContext` and `Decision`
   follow the same convention, and what their storage/crash-recovery
   story looks like, is implementation-level and not decided here.
   Deliberately left open rather than resolved by default to "yes,
   persist everything" — until it's clearer how the Relationship
   Engine behaves in practice (how often it runs, per Section 2.1;
   how large a `RelationshipContext` actually ends up being), committing
   to a storage model risks the same kind of premature decision this
   document already avoided once, in Section 3, by not inventing a
   Domain State snapshot object.
2. **How many `Decision`s can be "in flight" at once**, and how a
   Discretionary decision that depends on a conversation still in
   progress is represented, is not addressed — this document assumes
   one `RelationshipContext` produces one `Decision` synchronously, but
   does not rule out a richer model later.
3. **The exact triggers that make a situation "decision-worthy" at
   all** (as opposed to Domain State simply changing with no decision
   needed) are not enumerated here — related to, but distinct from,
   Section 2.1's event-type curation for trigger category B: 2.1
   decides *when the pipeline runs at all*; this question decides,
   once it has run, *whether the result is "no action" or something
   real* (already anticipated as a normal outcome in Section 5.1, but
   not given selection criteria here).

## Closing Note: Phase 0 Scaffolding

`database/models.py`'s `CoachAssessment`, `KeyholderAssessment`,
`DecisionResult`, `TrustState`, `RewardState`, and `ImpactScore` (with
backing tables since migration 001) describe a structurally different
shape — two independent engines each producing their own persisted
Assessment, reconciled afterward by a separate Decision Engine — and
were never wired into any working code (`core/coach_engine.py`,
`core/keyholder_engine.py`, and `core/decision_engine.py` do not exist;
`ai/` is empty). This document's shape (Coach and Keyholder as two
perspectives computed together inside one Relationship Engine, never
separately persisted as competing Assessments) supersedes that draft.

The Phase 0 tables are not deleted by this document — per
`database/migrations/README.md`'s own rule, retiring them requires the
multi-step process (new structure alongside old, switch over, keep the
old as backup for a release, only then remove it in an explicitly
labeled migration), not a direct `DROP TABLE`. This document simply
records that the *design* those tables were drafted for is superseded,
as a fact for the eventual implementation technical design to act on.

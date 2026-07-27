# Implementation Conventions

> This document answers a different question than the nine documents
> that precede it. Those answer **what the system is** — the domain
> model, invariants, and boundaries of Penalty Engine, Trust Manager,
> Activity Authorization, Hygiene Privilege, Goal Management, Recovery
> Plan, and Extension, plus their integration (`system_state_machine.md`).
> This document answers **how this kind of system gets built
> consistently** — the engineering patterns that already appear,
> independently and repeatedly, across every one of those documents,
> extracted here once so a new module (or a line of production code)
> never has to reinvent them.
>
> Nothing in Part I below introduces new domain behavior — every pattern
> is already in use somewhere in this system; this document only names
> it, generalizes it, and gives it one canonical home. Part II is
> different and is labeled as such: conventions this system has chosen,
> not yet conventions it has proven through repeated, independent use.
>
> Status: **Architecture baseline — approved for implementation.**
> Superseded the earlier "draft for approval" status once the
> Observed/Prospective split, the named Interpretation Handoff Pattern,
> the module design methodology (Section 15), the
> Responsibility→Ownership→Data→Algorithm sequence, and the
> traceability discipline (Section 16) were all in place. This document
> — together with `philosophy.md`, `domain_glossary.md`, the seven
> domain design documents, and `system_state_machine.md` — is now the
> baseline the implementation is written against, not a proposal still
> awaiting changes.

---

## 1. Why This Document Exists Now, Not Earlier

Writing this earlier would have meant guessing at conventions before
they were proven necessary. Writing it now means every convention in
Part I has already been exercised, under review, across at least three
independent modules — it describes what this system's engineering
discipline *actually is*, not what it might aspire to be. The test for
inclusion in Part I was simple: **does this pattern already appear, in
the same shape, in more than one prior document?** If yes, it belongs
there, generalized. If a document needed something genuinely once, it
stays local to that document, not here.

---

# Part I: Observed Conventions

Everything in this part is extracted from patterns already proven,
independently, across this system's nine domain and integration
documents — not invented for this document.

## 2. The Single-Writer Ownership Rule

**Every stateful table has exactly one owning module.** No other module
writes to it, and no other module reads it directly — only through a
narrow, named, purpose-built function the owning module exposes.

This is `philosophy.md` 2.11 (Domain Interpretation) at the level of
code, not principle: a domain's raw facts are interpreted only by the
domain that owns them; another module may act only on the interpretation
already produced, never the raw observation.

**Established instances**, for reference when building a new one:

| Owning Module | Owns | Exposes (read-only, to others) |
|---|---|---|
| Trust Manager | `Incident`, `TrustEvidence`, `TrustDomainState` | `get_incident_assessment()`, `get_confirmed_incidents_since()`, `get_accountability_assessment()`-style reads (per consumer) |
| Penalty Engine | `penalty_windows`, `freeze_periods`, `incident_consumption`, `recovery_credit_ledger`, `recovery_credit_decisions` | `get_authorization_freeze_state()`, `get_penalty_window_relevant_domains()` |
| Activity Authorization | `ActivityAuthorizationDecision`, `ActivityAuthorizationSession` | (none needed yet — no other module reads it) |
| Hygiene Privilege | `DiscretionaryHygieneBreakGrant`/`Session`, `HygienePenaltyOverrideDetermination` | (none needed yet) |
| Goal Management | `Goal`, `GoalEvaluation`, `GoalAccountabilityAssessment`, `GoalNegotiation` | `get_accountability_assessment()` |
| Recovery Plan | `RecoveryPlan`, `RecoveryTask`, `RecoveryTaskCompletion` | `get_recovery_task_completion()`, `get_recovery_task()` |

**Naming convention for these functions:** `get_<noun>()` for a direct
read, `determine_<noun>()` when the function derives a value from
several reads rather than returning a stored row verbatim (e.g.,
`determine_penalty_window_override()`). Never `fetch_`, `load_`, or
`query_` for a cross-module boundary function — those verbs are used
internally, within a module, for its own private data access
(`_load_incident_evidence()`, `_load_penalty_window()`); the public,
cross-module verb is always `get_` or `determine_`.

**A new module checklist item:** before writing a table, ask "does any
other module need to know about this?" If yes, design the narrow read
function *before* the table's full internal shape is finalized — the
function's return type should expose only what the consumer actually
needs (see `ConfirmedIncidentSummary` as the pattern: a minimal
projection, not the full owning entity).

## 3. The Interpretation Handoff Pattern

This is more than an implementation convention — it is a genuine
architectural pattern, and it deserves its own citable statement:

> **Interpretation Handoff Pattern.** One module owns interpretation.
> Another module owns consequences. Interpretation crosses module
> boundaries; consequences never do.

It emerged independently across at least four separate module
boundaries in this system, which is what earns it this status rather
than merely being one more naming convention among many. The full
shape:

```
Domain A
    owns its own interpretation of its own raw facts
        |
        v
    publishes an append-only judgment / a domain event referencing it
        |
        v
Domain B
    reads the judgment through Domain A's narrow API -- never the raw facts
        |
        v
    decides, independently, under its own rules, whether/how to act
        |
        v
    writes only to its own tables
```

**Established instances:**

| Domain A (interprets) | Domain B (decides its own consequence) | Judgment Read Via |
|---|---|---|
| Trust Manager (`IncidentAssessment`, via `CONFIRMED` gating) | Penalty Engine (Extension: eligible? how much?) | `get_incident_assessment()` |
| Goal Management (`GoalAccountabilityAssessment`) | Trust Manager (`GOAL_PROGRESS`/`GOAL_SETBACK`?) | `get_accountability_assessment()` |
| Recovery Plan (`RecoveryTaskCompletion`) | Penalty Engine (`recovery_credit_ledger`: how many hours?) | `get_recovery_task_completion()` |
| Activity Authorization (`freeze_penalty_window` decision) | Penalty Engine (create/close the actual `freeze_periods` row) | `activity_authorization.committed`/`resume_requested` events |
| Penalty Engine / Trust Manager (state) | Hygiene Privilege (effective policy) | `get_penalty_window_relevant_domains()`, `get_trust_domain_state()` — read-only, no consequence written back at all |

**Why this shape, specifically, and not a simpler one:** a naive
alternative — Domain B reading Domain A's raw table directly — was tried
and rejected at least twice in this system's history
(`system_state_machine.md` Finding 1, the original v1 Goal design) and
both times produced the same failure: Domain B ends up needing fields
that only make sense in Domain A's own model, the two schemas drift
apart, or Domain B starts writing consequences that were really Domain
A's decision to make. The handoff shape prevents this structurally: **B
can never see more of A's data than A chose to expose, and A can never
be blamed for a decision B made** — the audit trail cleanly separates
"what A observed and concluded" from "what B decided to do about it."

**When building a new cross-module consequence:** do not write "Domain B
reads Domain A's table and decides X." Write, explicitly: "Domain A
publishes judgment J. Domain B reads J through a named function and
decides X from J alone." If you cannot name the narrow read function in
one line, the boundary is not yet clear enough to implement.

**A consumer handler must never call another module's narrow read
function either — not only its raw table.** Discovered during actual
implementation, not anticipated on paper (Fáze 2.4, wiring the first
real cross-module consumer): when two modules share one underlying
`infrastructure.database.Database` core, that core's single-open-
transaction guard is shared too. A consumer handler running inside
`consume_event()`'s already-open transaction cannot call `get_incident_assessment()`,
`get_confirmed_incidents_since()`, or any other transaction-opening
public method on a different module — doing so raises
`NestedTransactionError`, correctly, since two independent connections
against the same file mid-transaction is exactly the hazard
`infrastructure/database.py`'s single-open-transaction guard exists to
prevent (a mechanism this document predates — `implementation_conventions.md`
was frozen before `infrastructure/database.py` existed as code; this
paragraph is the first place that guard's cross-module consequence is
written down).

The resolution generalizes the rule already stated above one level
further: **the event payload itself must carry everything a known or
reasonably anticipated consumer needs** — not only enough to identify
the judgment, but the judgment's actual content, so a handler is
transactionally self-contained and never reaches back into the
publisher's live API mid-reaction. Concretely, in this system:
`incident.confirmation_changed`'s payload grew, across two separate
integrations, to carry `trust_domain` (Fáze 2.4, Penalty Engine's
window-starting), then `rule_group_id`, `intrinsic_severity`, and
`cooperation_*` (Fáze 2.5, Extension's `should_extend()`) — each
addition driven by an actual consumer's actual need discovered while
wiring it, not anticipated in advance. This is the correct order to
discover a payload's full shape in: add a field when a real consumer
needs it, never speculatively for a consumer that does not exist yet.

A narrow read function's public API (`get_incident_assessment()`, etc.)
remains fully legitimate for **any caller outside an open transaction**
— a top-level function call, a test, a manual/administrative operation.
The constraint above applies specifically inside a consumer handler
already running inside `consume_event()`'s transaction, where a second,
independent transaction against the same database file is the hazard,
not the read itself.

## 4. The Transaction Pattern (`_apply_transition`)

Every state-changing operation in this system writes its data mutation
and its corresponding `domain_event` in **one** database transaction.
This is `philosophy.md` 2.8 (Technically Enforceable Auditability) made
concrete:

```python
def _apply_transition(db: Database, mutation: Callable, event: DomainEvent) -> None:
    """
    The canonical shape. Every module's version of this function is
    named differently (_apply_transition, _record_evaluation,
    _apply_goal_lifecycle_transition, _insert_extension_decision +
    _write_event, etc.) but is structurally identical: one
    `with db._connect() as conn:` block, the mutation first, the event
    write second, both inside it.
    """
    with db._connect() as conn:
        mutation(conn)
        _write_event(conn, event)
    # commit happens at the end of the `with` block -- a crash between
    # mutation() and _write_event() rolls back BOTH, never one without
    # the other.
```

**Convention:** a function that changes state and does *not* write a
corresponding event in the same transaction is a defect, not a stylistic
choice — every reviewed document in this system enforces this as an
explicit invariant (I18, TI12, GOAL-equivalent items, EXT-9, RP-implicit
via the same pattern). When implementing, treat "does this transaction
also write its event?" as a mandatory checklist item on every pull
request touching state.

**Where this pattern gained a second write inside the same transaction:**
`confirm_incident()` (`trust_manager_technical_design.md` 14.2) — when a
transition has a necessary, inseparable side effect (assessment
following confirmation), that side effect goes in the *same*
transaction, not a "usually right after" call. The test for whether two
writes belong in one transaction: **would a user-visible inconsistency
result if a crash landed exactly between them?** If yes, one
transaction. If no (e.g., Recovery Plan reacting to a Penalty Window
event, which can legitimately lag by the outbox's normal delivery
latency), separate transactions connected by an event are fine.

## 5. The Transactional Outbox

One shared table, `domain_events`, used by every module for every
cross-module event, never reimplemented per module.

- **Write:** in the same transaction as the state change (Section 4).
- **Claim:** a publisher process claims unclaimed/expired-claim rows
  (`claimed_at`/`claim_expires_at`) before delivering them — safe with
  multiple concurrent publisher processes
  (`penalty_window_technical_design.md` 4.6).
- **Publish:** `published_at` is set only after successful handoff to
  the transport layer. This means **at-least-once delivery, never
  exactly-once** — a consumer that assumes single delivery is a defect.
- **Consume:** every consumer checks `domain_event_consumers`
  (`UNIQUE(event_id, consumer_name)`) before acting, and records having
  processed the event in the same transaction as its own reaction (the
  `_apply_transition` pattern applied to consumption, not just
  production).

**Convention for a new event type:** name it `<module>.<verb_past_tense>`
(`goal.created`, `activity_authorization.committed`,
`recovery_plan.task_completed`) — never a present-tense or noun-only
name. The `source_module` field always names the module that changed
state, not the module that will eventually consume it.

## 6. Idempotency Conventions

Two layers, used together, never one alone:

1. **Client-generated identity for anything a user or another system
   might retry** — `request_id`, `confirmation_command_id`,
   `start_command_id`, `completion_id`. `UNIQUE` in the database.
   Never a heuristic key derived from timestamp + content (this was
   tried once, in an early Activity Authorization draft, and explicitly
   rejected — see that document's Section 4.0 history).
2. **A database uniqueness constraint on the *consequence*, not just the
   request** — `UNIQUE(source_entity_type, source_entity_id,
   evidence_type)` on `TrustEvidence` (TI25), `UNIQUE(completion_id)` on
   `recovery_credit_decisions` (I26), `UNIQUE(evaluation_id)` on
   `GoalAccountabilityAssessment` (GOAL-13). This is the layer that
   protects against a *different* retry path than the client's — e.g., a
   redelivered event with a distinct `event.id` that would otherwise
   sail past the consumer-dedup check and attempt the write again.

**Convention:** when a function produces a side effect that must happen
at most once for a given input, ask both questions — "is there a
client-facing retry path?" (→ layer 1) and "could the *same* input
reach this function twice through *different* paths?" (→ layer 2).
Most of this system's cross-module integrations need both.

## 7. Append-Only Versus Mutable-With-Status

Two, and only two, shapes are used for stateful entities in this
system. Picking the wrong one for a new entity is the single most
common design error this system's review process caught early
(Trust Manager v1's `applied` flag on `TrustEvidence`; Activity
Authorization v1's implicit mutability of what should have been
history).

**Append-only** (a fact, once true, is always true — corrections are
new rows, never edits): `TrustEvidence`, `TrustRecalculation`,
`ConfirmationRecord`, `GoalEvidence`, `GoalVersion`,
`GoalAccountabilityAssessment`, `ExtensionDecision`,
`RecoveryCreditDecision`, `HygienePenaltyOverrideDetermination`,
`domain_events` itself.

**Mutable-with-status** (a genuine lifecycle, where "what is true right
now" is the only thing that matters, and history lives in the
accompanying append-only event trail, not in old field values):
`penalty_windows.status`, `ActivityAuthorizationDecision.lifecycle_status`,
`Goal.status`, `DiscretionaryHygieneBreakGrant.status`,
`GoalNegotiation.status`, `RecoveryPlan.status`.

**The test:** if a future reader would ever need to ask "what did this
look like *before* it changed," it is append-only. If the only
meaningful question is "what is it *now*, and how did it get here"
(answerable from the event log, not the row itself), mutable-with-status
is correct and append-only would be unnecessary overhead.

## 8. Naming Conventions

- **Cross-module read functions:** `get_<noun>()` (a direct lookup) or
  `determine_<noun>()` (a derived decision over several reads). Section
  2.
- **Internal, module-private reads:** `_load_<noun>()`.
- **State-changing functions:** a verb describing the domain action,
  not the mechanism — `consume_incident_for_active_window()`, not
  `update_incident_table()`; `record_goal_assessment_evidence()`, not
  `insert_trust_evidence_row()`.
- **Recovery functions:** always `recover_<module>_state(db, now)`,
  always taking `now` from the injected `Clock`
  (`activity_authorization_technical_design.md` 16.7), never calling a
  system clock directly.
- **Events:** `<source_module>.<verb_past_tense>` (Section 5).
- **Tables:** plural nouns, snake_case (`penalty_windows`,
  `recovery_credit_ledger`, `incident_consumption`). A join/consumption
  table that exists purely to record "A happened to B" is named
  `<a>_<b>` or `<a>_consumption`/`<a>_decisions`, not a generic
  `_link`/`_map` suffix.
- **Invariant IDs:** a short module prefix plus a number
  (`I<n>` Penalty Engine, `TI<n>` Trust Manager, `GOAL-<n>`,
  `EXT-<n>`, `HYG-<n>`, `RP-<n>`, `SSM-<n>` System State Machine).
  Never reused across modules even if the number would otherwise be
  free — `I26` and `TI26` are different invariants in different
  documents; no invariant ID is ever assumed unique system-wide without
  its prefix.

## 9. Crash/Restart Recovery Conventions

Every module that owns any non-terminal, timeout-bearing, or
multi-step state defines its own `recover_<module>_state(db, now)`,
following the same shape:

1. Find rows in a non-terminal status.
2. For each, compare its absolute deadline field (never an in-memory
   timer — `philosophy.md` 2.8 applied to time itself) against `now`.
3. Transition expired ones to their defined terminal/failure state,
   compensating (`REVERSAL`-style) if a resource was provisionally
   committed.
4. Leave still-valid ones untouched.
5. Be safe to call any number of times in a row (idempotent by
   construction — status-guarded transitions, not a one-shot script).

A module with **no** non-terminal state (Recovery Plan, Goal
Management's `GoalNegotiation.OPEN`) still gets a `recover_` function,
but it performs a consistency check rather than a timeout sweep — see
`recovery_plan_technical_design.md` 8 for the pattern of "this function
exists to detect an anomaly, not to be the normal path that fixes it."

**The complete, ordered sequence across all modules is owned by the
runtime/bootstrap layer, not by any domain module** — see
`system_state_machine.md` Section 7, `on_system_startup()`. A new
module's recovery function gets added there, in dependency order (does
it read another module's post-recovery state? if so, it goes after
that module's step).

## 10. Concurrency and Locking Conventions

- **Per-user/per-resource write serialization** (preventing two
  concurrent requests from jointly violating a capacity constraint):
  `BEGIN IMMEDIATE` or an equivalent restart-safe mechanism, applied at
  the point of the *binding* check, never only at a preliminary,
  advisory check earlier in the flow (`commit_authorization()`'s
  DA1-CONCURRENT re-check; `start_hygiene_break_session()`'s quota
  re-check).
- **Never an in-memory mutex for anything that must survive a
  restart.** This is stated as its own invariant in at least three
  documents (LEASE-1, the Hygiene Privilege equivalent, the general
  principle in `activity_authorization_technical_design.md` 8.2) because
  an in-memory lock silently disappears exactly when a crash makes
  correctness matter most.
- **The system-wide startup lease** (`system_startup_lease`,
  `system_state_machine.md` Section 7) is the *only* mechanism
  protecting against two process instances performing recovery
  simultaneously. A new module's recovery function does not need its
  own equivalent — it runs inside that lease.
- **The outbox claim mechanism** (Section 5) is independent of the
  startup lease and protects a different thing: the *ongoing* publisher
  process against processing the same row twice, even outside of
  startup.

## 11. Testing Conventions

Every module's test matrix in this system already follows one shape:
**Given / When / Then, one row per invariant-linked scenario**, with the
invariant ID cited in the "Then" column. This is not incidental — it is
what makes it possible to check, mechanically, whether every invariant
has at least one test exercising it (a check worth actually running
before implementation begins: does every `I<n>`/`TI<n>`/`GOAL-<n>`/etc.
across all nine documents appear in at least one test row?). A new
module's test matrix should be written in this exact shape from the
start, not adapted to it afterward.

Restart/crash scenarios get their own sub-matrix (`RT<n>`, `HRT<n>`,
`ET1x`-style), separate from ordinary behavioral tests, because they
exercise the recovery functions in Section 9, not the module's normal
request-handling path — conflating the two in one table has, in this
system's own history, made it harder to tell whether a given test is
checking "does this work" versus "does this survive a crash," which are
different questions with different failure modes.

---

# Part II: Prospective Conventions

Everything in this part is different in kind from Part I: no code
exists yet, so none of it has been exercised. These are conventions this
system has **chosen**, by analogy with patterns already proven
elsewhere in the domain documents, not conventions it has **proven**
through repeated, independent use. Treat them as a starting default,
open to revision once real implementation and integration testing
produce evidence — which is exactly the process that produced every
convention in Part I.

## 12. Database Migration Conventions

- **Migrations are additive by default.** Adding a column, a table, or
  an index is a normal migration. Removing or renaming a column that any
  shipped code reads is a `critical_change`-adjacent event — requires
  the same "why before how" scrutiny as a `philosophy.md` change, even
  though it is not literally one, because it can silently break a
  module that reads the old shape.
- **A schema change that moves ownership of a field between modules**
  (as Finding 1 did, moving `Incident`'s consumption tracking) is
  implemented as: (1) add the new, correctly-owned table/column; (2)
  migrate existing data into it; (3) stop writing the old
  field/table; (4) remove the old field/table only in a later,
  separate migration, once nothing reads it. Never a single migration
  that does all four at once — this system's own Finding 1 fix is the
  worked example to follow, even though that fix was applied to a
  design document, not a live schema; the same discipline should govern
  the eventual real migration.
- **Every migration is forward-only in production.** A rollback is a
  new, forward migration that undoes the change, never a reverse-apply
  of the original migration file — consistent with this system's
  append-only philosophy applied to the schema's own history.

## 13. Error Handling and Retry Policy

**Distinguish domain outcomes from technical failures — they are not
the same kind of thing, and this system already treats them
differently everywhere in its design, even though no error-handling
runtime yet exists to enforce it:**

- **A domain outcome** (`DENIED`, `FAILED_AT_START`,
  `INELIGIBLE_ISOLATED_LOW_SEVERITY`, `capacity_limited=True`) is a
  **normal, successful return value** — a decision the system made
  correctly, not an error. It is never raised as an exception, never
  logged as an error-level event, and always carries an `explanation`
  (Section 7's append-only decision records exist largely for this
  reason).
- **A technical failure** (a lock timeout, a network error to the LLM
  provider, a database connection drop) is an actual exception,
  propagated normally, and is what retry policy applies to — never to a
  domain outcome. Retrying a `DENIED` decision by calling the function
  again with the same input is a bug; retrying a timed-out database
  connection is normal operation.
- **Idempotent commands (Section 6) make retries safe by construction**
  — the retry policy for a technical failure on a write is simply "the
  caller may resend the identical command with the same client-generated
  ID," never "the caller must first figure out what partially happened."
- **`StartupLeaseNotAcquired`, `PenaltyWindowNotFound`** and similar
  named exceptions in this system are raised for conditions the caller
  is expected to handle explicitly (wait, or query correctly) — they
  are part of the interface, not incidental technical failures. A new
  module's public API should distinguish these the same way: a
  `None`/empty return for "not found, and that's fine," a named
  exception only for "this should not be possible given the caller's
  own precondition."

## 14. Event Versioning

- **Event payloads only grow.** A new field is always optional to
  existing consumers; removing or repurposing a field requires a new
  `event_type` string (e.g., `goal.created.v2`), never a silent change
  to what an existing `event_type` means.
- **Consumers ignore unknown fields.** A consumer's deserialization
  must not fail on a payload field it does not recognize — this is what
  makes additive payload evolution safe without coordinating every
  consumer's deployment.
- **A breaking change to an event's meaning is treated as introducing a
  new event type, with the old one deprecated, not replaced in place** —
  both may coexist during a migration window, exactly as a `GoalVersion`
  or `ActivityPolicy` version coexists with its predecessor until
  nothing references the old one.

---

# Part III: Applying All of This

## 15. Checklist for a New Module

The eight points below are not eight independent questions to answer in
any order — they follow one deliberate sequence, and the sequence
itself is worth naming: **responsibility, then ownership, then data,
then algorithm.** This is the reverse of how design commonly proceeds
(a data model or a class diagram first, behavior fitted around it
afterward). In every module this system built, the data model became
close to a mechanical consequence once points 1–3 were genuinely
settled — and every time a module's data model felt effortful or
required rework, it was because one of the first three points had been
skipped or answered too vaguely, not because the data itself was hard.
When starting a new module, resist the pull to open with a
`@dataclass` — write points 1–3 as prose first, even one sentence each.

Distilled from the fact that every module in this system, independently,
converged on the same shape (first observed explicitly in
`recovery_plan_technical_design.md` Section 1). This is not merely a
checklist — it is this system's actual design methodology for a new
module, and should be treated as a mandatory starting point, not an
optional aid, especially if this project ever has more than one author.

1. **What single question does this module answer?** If you cannot
   state it in one sentence, the module's scope is not yet clear enough
   to design.
2. **What state does it own?** (Section 2 — exactly one writer per
   table, no exceptions.)
3. **What interpretation does it own?** (Section 3 — what raw facts
   does it turn into a judgment, and for whom.)
4. **What narrow, named API does it expose to other modules?** (Section
   2's naming convention.)
5. **What events does it publish, and in what past-tense name?**
   (Section 5.)
6. **What must it never do?** — every prior module's document states
   this explicitly and early (Activity Authorization 2.5, Hygiene
   Privilege 2.5-equivalent, Goal Management 2.5, Recovery Plan 2.5).
   Write this before the data model, not after — it is usually easier
   to state what a module must never do than to derive it later from a
   half-finished schema.
7. **Does it own any non-terminal, timeout-bearing state?** If yes,
   Section 9 applies in full (a `recover_` function, absolute
   timestamps, idempotent reconciliation). If no, it still gets a
   `recover_` function, but a minimal, consistency-check-only one.
8. **Does any cross-module consequence flow through it?** If yes,
   Section 3 (the Interpretation Handoff Pattern) applies — name the
   judgment type and the read function before writing the consequence
   logic.

A module that can answer all eight points before its first data class
is written will very likely need little architectural rework later —
every module in this system that skipped one of these (most visibly,
Activity Authorization v1 and Goal Management v1) needed a second or
third review round specifically to correct the point it had skipped.

## 16. Traceability Discipline for Implementation

One rule, kept deliberately small: **every pull request should be
citable against a specific invariant, pattern, or section of this
document set** — "implements TI21," "applies Section 3, the
Interpretation Handoff Pattern," "implements I26." A change that cannot
be cited this way is either implementing something this documentation
does not yet cover (in which case the documentation should be updated
in the same change, not left to drift silently) or is not actually
grounded in the design at all.

This is not a bureaucratic constraint on implementation — it is what
keeps design and code from quietly diverging over time, which is the
one failure mode a documentation set this thorough cannot protect
against by itself. Nothing else in this document set can enforce this;
it is a discipline for whoever writes the code, not something the
architecture can guarantee on its own.


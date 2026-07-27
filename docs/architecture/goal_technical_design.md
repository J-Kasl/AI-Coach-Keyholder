# Goal — Technical Design (v4)

> **v4:** applied the Trust Manager integration (Section 11) — added
> `get_accountability_assessment()`, the concrete
> `goal_accountability_assessment.recorded` event, and corrected
> GOAL-10 to reflect that the event is published for every assessment,
> not pre-filtered by `relevant_to_trust`. Also added Section 13
> (Domain Events, consolidated) — appended rather than inserted, so
> that no other document's cross-references to this document's section
> numbers needed to change.
>
> **v3 — fix from the `system_state_machine.md` integration audit
> (Finding 3):** `GoalNegotiationStatus` gains a `MOOT` terminal value.
> Whenever a Goal's lifecycle transitions to `COMPLETED`/`ABANDONED`/
> `REPLACED` while a `GoalNegotiation` on it is still `OPEN`, that
> negotiation is automatically closed as `MOOT` in the same
> transaction (GOAL-14) — it is never left orphaned, referencing a
> Goal that can no longer act on its outcome.
>
> Draft for review, **not implemented**. Based on `philosophy.md` v1.11
> Section 2.9 (Accountability Versus Development) and Section 2.10
> (Rule Violations Versus Goal Failures). `Goal` is a first-class domain
> entity alongside `Rule` and `Incident` — see `domain_glossary.md`
> v1.3, which defines `Goal`, `Rule`, `Goal Outcome`, `Goal Failure`, and
> `Goal Success` as official terms.
>
> **v2 — fixes from review:** (1) the accountability judgment ("is this
> relevant to Trust, and does it indicate progress or a setback") is
> now made by a separate, Keyholder-owned `GoalAccountabilityAssessment`
> — the Coach's artifact (renamed `GoalInvestigation` →
> `GoalEvaluation`) answers only the development question and carries
> no accountability field at all, correctly implementing 2.9 instead of
> merely citing it; (2) `GoalEvaluation` is generalized to respond to
> any Goal Outcome (not only `MISSED`/`PARTIALLY_MET`), giving
> `GOAL_PROGRESS` a real source; (3) Goal Management never writes
> `TrustEvidence` — it only publishes a completed, Trust-relevant
> `GoalAccountabilityAssessment`; the Trust Manager remains the sole
> owner of every `TrustEvidence` write, consistent with its role
> elsewhere in this system; (4) `ARCHIVED` is no longer a
> `GoalLifecycleStatus` value — it is a separate `archived_at` field, so
> a Goal's true terminal outcome (`COMPLETED`/`ABANDONED`/`REPLACED`) is
> never lost; (5) negotiation now supports multiple rounds
> (`GoalNegotiation` + append-only `GoalNegotiationRound`), matching
> `philosophy.md`'s "negotiate until they reach..." rather than a single
> exchange, and never carries a `final_intervention` while unresolved;
> (6) `GoalChangeProposal` now references an immutable
> `GoalChangeProposalContent` payload — acceptance applies exactly the
> reviewed content, satisfying 2.5's requirement that consent be bound
> to a specific change; (7) `GoalPeriodOutcome` is renamed `GoalOutcome`
> and explicitly documented as the parent of Goal Success/Goal Failure,
> with `PARTIALLY_MET` as neither.
>
> **Second review — three further fixes, one resolved question:** (1)
> `objects_to_proposed_intervention`/`objection_reason` renamed to
> `review_outcome` (`ACCEPT`/`NEGOTIATE`) + `negotiation_reason` —
> Coach and Keyholder are not opposing parties per 2.9, and negotiation
> can be warranted without disagreement (e.g., a significant change
> worth a joint conversation); (2) added GOAL-13 — at most one
> `GoalAccountabilityAssessment` per `GoalEvaluation`; a revised
> judgment requires a new `GoalEvaluation`; (3) clarified that Goal
> Management is a domain module for `Goal`, not "the Coach's module."
> **Resolved:** `GoalAccountabilityAssessment` stays in Goal
> Management — not because of module-coupling concerns, but because it
> is, in substance, an assessment *of a `GoalEvaluation`*, and therefore
> belongs to the Goal domain regardless of whether it ever produces a
> Trust effect. See Section 6.2.
>
> Status: **Architecture baseline — approved for implementation.**
> Reached this status once Accountability was structurally separated
> from Development (v2), the applied Trust Manager integration and
> consolidated Domain Events section (v4), and the `MOOT` negotiation
> fix (v3) were all in place — this document is now the baseline for
> Goal Management's implementation, not a proposal still awaiting
> changes.

---

## 1. Why Goal Is Its Own Domain Entity

`philosophy.md` 2.10 distinguishes Rules (binding boundaries, enforced
by the Keyholder, whose violation is an Incident) from Goals (agreed
directions of development, supported by the Coach, whose failure is
never itself an Incident). Section 2.9 goes further and is the
organizing principle for this entire document: both perspectives may
reason about the *same* underlying facts, but ask different questions
of them — the Keyholder asks what level of autonomy is justified by
demonstrated responsibility; the Coach asks what intervention most
improves long-term success. **Every data structure below that touches
both perspectives keeps their outputs in physically separate records,
never a shared field one perspective could silently fill in for the
other.**

```
Rule     -- a binding behavioral boundary (Keyholder-owned)
Goal     -- an agreed direction of development (Coach-owned)
Incident -- a confirmed Rule violation (the only trigger for a Penalty Window)
```

`Goal` is owned by a new module, referred to here as **Goal
Management**. This is deliberately *not* "the Coach's module" — it is
the domain module for `Goal` itself, the same way the Trust Manager is
the domain module for `Trust`, not "the Keyholder's module." The Coach
writes `GoalEvaluation`; the Keyholder writes
`GoalAccountabilityAssessment` (Section 6) — both live inside Goal
Management because both are about the same Goal-centered situation, not
because either perspective owns the module. Goal Management:

- **reads** nothing from the Trust Manager for any decision of its own.
- **never writes** to `penalty_windows`, `freeze_periods`, `incidents`,
  or any Trust Manager table — including `TrustEvidence` (fix for
  Point 3; see Section 11). A Goal Failure structurally cannot reach a
  Penalty Window, and Goal Management structurally cannot reach into
  Trust's own tables — both are enforced the same way, by never
  granting the write path in the first place.
- **is read by** the Trust Manager (Section 11) and, eventually, by a
  check-in/Coach conversation mechanism not designed in this document.

---

## 2. The Goal Model

### 2.1 Positive Definition (`philosophy.md` 2.10)

> A Goal is an agreed direction of development whose purpose is to
> improve the user's long-term trajectory, not to define a binding
> behavioral boundary.

From this, the following follow directly, without reference to what a
Goal is *not*:

- A Goal's outcome for a period can be a Goal Success, a Goal Failure,
  or a partial result that is neither (2.6) — never a "violation."
- A Goal can be **adapted** (its target adjusted while the underlying
  direction stays the same) or **replaced** (a new Goal better suited
  to the same underlying direction).
- A Goal can be judged **no longer relevant** and abandoned — a
  legitimate, unremarkable outcome, not a failure of the user or the
  system.
- A Goal always belongs to exactly one Trust domain (Section 11.1) —
  the domain its outcomes are eventually relevant to, even though
  missing it is never itself a Rule violation.

### 2.2 Data Structures

```python
@dataclass(frozen=True)
class GoalVersion:
    """
    Append-only. Represents the CONTENT of a Goal at a point in time --
    its title and target. Adapting a Goal (2.4) creates a new
    GoalVersion under the same goal_group_id; it never edits an
    existing one.
    """
    id: str
    goal_group_id: str            # stable across versions/adaptations
    version: int
    title: str                     # e.g., "Exercise several times per week"
    target_description: str        # e.g., "3 workouts per week" -- human-readable, not a formal predicate
    trust_domain: str              # which Trust domain this Goal's outcomes are relevant to (Section 11.1)
    created_at: datetime
    created_via: str                # 'user_proposed' | 'coach_proposed_user_approved' | 'coach_initial_setup'
    adaptation_reason: str | None   # REQUIRED if version > 1 -- why this version replaced the previous one
    supersedes_id: str | None       # FK to the GoalVersion this one replaces, None for version 1
```

```python
class GoalLifecycleStatus(StrEnum):
    """
    ARCHIVED deliberately does NOT appear here (fix for Point 4) -- see
    2.6. Every value here is a genuine behavioral state, not a
    presentation concern.
    """
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ABANDONED = "abandoned"
    REPLACED = "replaced"        # superseded by an entirely new Goal (new goal_group_id), not merely adapted


@dataclass
class Goal:
    """
    MUTABLE current-state record for a goal_group_id -- the same
    pattern as penalty_windows.status. Every transition is atomic with
    its domain_event (Section 9).
    """
    goal_group_id: str
    current_version_id: str          # FK to the active GoalVersion
    status: GoalLifecycleStatus
    created_at: datetime
    status_changed_at: datetime
    replaces_goal_group_id: str | None   # populated only if this Goal REPLACED an earlier one (2.4)
    archived_at: datetime | None          # independent of status -- see 3.3, GOAL-11
```

`GoalVersion.adaptation_reason` being required for any version beyond
the first mirrors `TrustRecalculation.explanation`: a change without a
recorded reason is not a valid change anywhere in this system.

### 2.3 Creation

A Goal always begins as `GoalVersion(version=1)` plus a new `Goal` row
with `status=ACTIVE`. Per `philosophy.md` 2.5, a Goal is always
something the user has agreed to, even when the Coach proposes it --
`created_via='coach_proposed_user_approved'` requires a prior
confirmation step (Section 5.3), lighter than the full `critical_change`
consent flow reserved for Rules and system parameters, but still a real
confirmation bound to specific, recorded content (fix for Point 6).

### 2.4 Adaptation Versus Replacement

- **Adaptation** -- the underlying direction is still right, but the
  target needs adjusting (e.g., "3 workouts/week" -> "2 workouts/week"
  during an exam period). Creates a new `GoalVersion` under the *same*
  `goal_group_id`. `Goal.status` is unaffected.
- **Replacement** -- the Goal itself was misshapen, not merely
  miscalibrated. The current `Goal.status -> REPLACED`, and an entirely
  new `Goal` (new `goal_group_id`) is created with
  `replaces_goal_group_id` pointing back to it.

Both are Coach-proposed, user-confirmed actions against specific,
recorded content (Section 5.3) -- neither happens silently, and neither
is reconstructed from "whatever the current conversation implies" at
the moment of acceptance.

### 2.5 What Goal Failure and Goal Success Mean

Per `domain_glossary.md` v1.3: a **Goal Outcome** is the result of
evaluating a Goal's target against a specific period. **Goal Success**
is the Goal Outcome value `MET`; **Goal Failure** is the value
`MISSED`. A third value, `PARTIALLY_MET`, is neither a Goal Success nor
a Goal Failure -- a distinct, intermediate outcome (fix for Point 7; see
2.6). All three are properties of a specific evaluation *period* against
a specific `GoalVersion`'s target, not terminal states of the Goal
itself. A Goal can accumulate any number of each across its lifetime
while remaining `ACTIVE` throughout; only a separate, explicit lifecycle
transition (3.2) ends it.

### 2.6 `GoalOutcome` (Renamed From `GoalPeriodOutcome`, Fix for Point 7)

```python
class GoalOutcome(StrEnum):
    """
    The parent concept behind domain_glossary.md's Goal Success and
    Goal Failure. MET corresponds to Goal Success; MISSED corresponds
    to Goal Failure; PARTIALLY_MET is neither -- it is its own,
    distinct outcome, not a subtype of failure.
    """
    MET = "met"                     # Goal Success
    PARTIALLY_MET = "partially_met"  # neither Goal Success nor Goal Failure
    MISSED = "missed"                # Goal Failure
```

---

## 3. Goal Lifecycle

### 3.1 States

```
                    +--------+
        create() -->| ACTIVE |<------------+
                    +---+----+              |
                pause() |      ^ resume()   | adapt() (no state change)
                    +---v----+              |
                    | PAUSED |              |
                    +---+----+              |
                resume()|                   |
                        +-------------------+
                    +-----------+
        ACTIVE ---->| COMPLETED |
                    +-----------+
                    +-----------+
        ACTIVE ---->| ABANDONED |
        or PAUSED    +-----------+
                    +----------+
        ACTIVE ---->| REPLACED |--> (a new Goal is created, see 2.4)
        or PAUSED    +----------+
```

`ARCHIVED` no longer appears on this diagram (fix for Point 4) -- see
3.3. `adapt()` does not itself change `Goal.status`.

### 3.2 Transition Guards

| Transition | Guard | Side Effects |
|---|---|---|
| `(none) -> ACTIVE` | user confirmation obtained (2.3) | creates `Goal` + `GoalVersion(version=1)`; emits `goal.created` |
| `ACTIVE -> PAUSED` | Coach-proposed or user-initiated; a reason is recorded | `status_changed_at=now`; emits `goal.paused` |
| `PAUSED -> ACTIVE` | -- | `status_changed_at=now`; emits `goal.resumed` |
| `ACTIVE/PAUSED -> COMPLETED` | the Goal's aspiration has been durably achieved (a judgment call -- see 3.4) | emits `goal.completed` |
| `ACTIVE/PAUSED -> ABANDONED` | Coach-proposed, user-confirmed against specific content (5.3) | emits `goal.abandoned` |
| `ACTIVE/PAUSED -> REPLACED` | Coach-proposed, user-confirmed (5.3); creates the replacement Goal (2.4) in the same transaction | emits `goal.replaced` |

### 3.3 `archived_at` Is a Separate, Presentation-Only Field (Fix for Point 4)

The v1 design modeled archiving as a lifecycle status
(`ACTIVE/.../REPLACED -> ARCHIVED`), which -- as review correctly
identified -- destroyed the information the claim "archiving has no
behavioral meaning" depended on: once archived, a caller could no
longer tell whether the Goal had been `COMPLETED`, `ABANDONED`, or
`REPLACED`. Fixed by making `archived_at` an independent field on
`Goal` (2.2):

```python
def archive_goal(db: Database, goal_group_id: str, now: datetime) -> None:
    """
    Sets Goal.archived_at. Requires Goal.status to already be terminal
    (COMPLETED/ABANDONED/REPLACED) -- GOAL-11. Never changes status.
    Does not require a GoalChangeProposal (Section 5.3): archiving
    changes no content and no meaning, only visibility, so the lighter
    Consent & Control bar that applies to substantive Goal changes does
    not apply here.
    """
```

### 3.4 `COMPLETED` Is a Deliberate, Not Automatic, Transition

A Goal has no built-in notion of "done" the way a countdown does.
`ACTIVE -> COMPLETED` is always a Coach-proposed, user-confirmed
judgment (5.3), for goals with a natural endpoint, not a default outcome
for ongoing, indefinite Goals.

---

## 4. Goal Evidence

### 4.1 Why This Is Not Trust Evidence

`GoalEvidence` is defined entirely on Goal's own terms -- it records
what happened relative to a Goal's target, in a specific evaluation
period. It says nothing about Trust or accountability yet. Whether, and
how, it eventually contributes to a Trust recalculation is decided much
later, by two independent, explicit steps (Sections 5 and 6) -- never
automatically and never by this section.

```python
@dataclass(frozen=True)
class GoalEvidence:
    """
    Append-only. Represents ONE evaluation period's outcome against ONE
    GoalVersion's target.
    """
    id: str
    goal_group_id: str
    goal_version_id: str          # which version's target was being evaluated
    period_start: datetime
    period_end: datetime
    outcome: GoalOutcome           # renamed from GoalPeriodOutcome, see 2.6
    observed_progress: str         # human-readable account of what actually happened
    source: str                    # 'check_in' | 'user_report' | 'system_derived'
    created_at: datetime
```

### 4.2 What Generates Goal Evidence

Out of scope for this document, as in v1: the check-in/conversation
mechanism that actually observes progress and produces `GoalEvidence`
rows. This document defines the shape evidence must take; the mechanism
that populates it is a separate, follow-up piece of work.

### 4.3 No Single Piece of Evidence Is, by Itself, a Finding (Generalized, Fix for Point 2)

A `GoalEvidence` row -- of **any** outcome, `MET` included, not only
`MISSED`/`PARTIALLY_MET` -- is a fact, not a verdict. It becomes
actionable only once a `GoalEvaluation` (Section 5) has looked at it. A
single period, regardless of direction, never by itself triggers
anything (GOAL-2, Section 8). This symmetry is what gives
`GOAL_PROGRESS` a real source (v1's gap, per Point 2): a pattern of
sustained `MET` periods can be evaluated exactly the same way a pattern
of `MISSED` periods can -- the pipeline no longer only exists for
problems.

---

## 5. Coach Workflow -- the Development Question Only

### 5.1 `GoalEvaluation` (Renamed From `GoalInvestigation`, Generalized, Fix for Point 2)

```python
class GoalInterventionType(StrEnum):
    ADAPT_TARGET = "adapt_target"                # 2.4 adaptation
    INCREASE_SUPPORT = "increase_support"          # e.g., more frequent check-ins -- does not change the Goal itself
    NO_CHANGE = "no_change"                         # evaluated, judged temporary, acceptable, or worth simply acknowledging
    PROPOSE_REPLACEMENT = "propose_replacement"     # 2.4 replacement
    PROPOSE_ABANDONMENT = "propose_abandonment"     # 3.2 ABANDONED


@dataclass(frozen=True)
class GoalEvaluation:
    """
    Append-only. The Coach's structured response to one or more
    GoalEvidence records, of ANY outcome -- philosophy.md 2.9's
    development question made concrete: "what intervention is most
    likely to improve the user's long-term success?" This dataclass
    deliberately has NO field answering the accountability question
    (fix for Point 1, GOAL-9) -- that question belongs exclusively to
    GoalAccountabilityAssessment (5.4/6.1), which the Keyholder produces
    separately, reviewing this same evaluation rather than folding its
    own judgment into it.
    """
    id: str
    goal_group_id: str
    created_at: datetime
    triggering_evidence_ids: tuple[str, ...]   # non-empty, any outcome mix (GOAL-3)
    findings: str                               # what happened and why, in the Coach's own reasoning
    proposed_intervention: GoalInterventionType
    proposed_intervention_detail: str
```

### 5.2 From Evaluation to Action

| `proposed_intervention` | What Happens |
|---|---|
| `ADAPT_TARGET` | a new `GoalVersion` is proposed (2.4) -- subject to user confirmation (5.3) |
| `INCREASE_SUPPORT` | no change to the Goal itself; out of scope here |
| `NO_CHANGE` | no change; the evaluation itself is still recorded for audit |
| `PROPOSE_REPLACEMENT` | a replacement Goal is proposed (2.4) -- subject to user confirmation (5.3) |
| `PROPOSE_ABANDONMENT` | `ABANDONED` is proposed (3.2) -- subject to user confirmation (5.3) |

### 5.3 User Confirmation -- Bound to Specific Content, Not Just an Operation Type (Fix for Point 6)

Per `philosophy.md` 2.5, consent must be bound to a specific change, not
granted in general. The v1 design let a user confirm a
`GoalInterventionType` without confirming *what* the new target or
replacement actually was -- review correctly identified this as
insufficiently specific.

```python
class GoalProposalStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    EXPIRED = "expired"


@dataclass
class GoalChangeProposal:
    """MUTABLE. The operation type and its expiry -- the confirmable
    CONTENT lives in GoalChangeProposalContent, below."""
    id: str
    evaluation_id: str | None       # None if user-initiated rather than Coach-proposed
    goal_group_id: str
    proposed_change: GoalInterventionType
    proposal_expires_at: datetime
    status: GoalProposalStatus
    created_at: datetime
    resolved_at: datetime | None


@dataclass(frozen=True)
class GoalChangeProposalContent:
    """
    Append-only, immutable. The SPECIFIC content the user is actually
    confirming. Acceptance applies exactly this recorded payload --
    never a version reconstructed from whatever context exists at
    acceptance time (GOAL-6).
    """
    id: str
    proposal_id: str
    proposed_title: str | None                  # for ADAPT_TARGET / new-Goal creation
    proposed_target_description: str | None      # for ADAPT_TARGET / new-Goal creation
    proposed_replacement_goal_group_id: str | None   # for PROPOSE_REPLACEMENT, if the replacement Goal is created up front
    reason: str
```

A Goal's Trust domain is deliberately absent from
`GoalChangeProposalContent` -- Section 11.1 fixes it at creation;
changing it is out of scope for an adaptation/replacement proposal in
this version of the document (see Section 12, open questions).

An unresolved `GoalChangeProposal` that reaches `proposal_expires_at` is
reconciled the same way an expired Activity Authorization confirmation
is: transitioned to `EXPIRED`, with no effect (Section 9.3).

---

## 6. Accountability -- the Keyholder's Independent Judgment (Fix for Point 1)

### 6.1 `GoalAccountabilityAssessment`

This is the structural fix for the review's primary finding: Coach and
Keyholder look at the same `GoalEvaluation`, but produce **separate**
records answering their **own** guiding question (`philosophy.md` 2.9).

```python
class GoalAccountabilityDirection(StrEnum):
    PROGRESS = "progress"
    SETBACK = "setback"
    NEUTRAL = "neutral"      # reviewed, but not judged meaningfully indicative either way


class GoalAccountabilityReviewOutcome(StrEnum):
    """
    Deliberately not framed as 'objection'/'agreement' -- per
    philosophy.md 2.9, Coach and Keyholder are not opposing parties;
    they answer different questions. NEGOTIATE may be warranted even
    without any disagreement in substance -- e.g., because the proposed
    change is significant enough to warrant a joint conversation, not
    only because the Keyholder finds fault with it.
    """
    ACCEPT = "accept"        # the Coach's proposed handling stands as-is
    NEGOTIATE = "negotiate"  # opens a GoalNegotiation (Section 7)


@dataclass(frozen=True)
class GoalAccountabilityAssessment:
    """
    Append-only. The Keyholder's own record, answering ONLY the
    accountability question: "what level of autonomy is justified by
    the user's demonstrated responsibility?" (philosophy.md 2.9).
    References a GoalEvaluation -- never raw GoalEvidence directly
    (GOAL-8) -- because Accountability evaluates the Coach's findings
    in context, not isolated facts.

    Not every GoalEvaluation receives one of these -- per philosophy.md
    2.10, "normally, Goal Failures remain entirely within the Coach's
    responsibility." When and why the Keyholder chooses to review a
    given GoalEvaluation is intentionally left open (Section 12). At
    most ONE GoalAccountabilityAssessment may exist per GoalEvaluation
    (GOAL-13) -- a changed judgment requires a new GoalEvaluation, not a
    second assessment layered onto the same one, to keep the audit
    trail unambiguous.
    """
    id: str
    evaluation_id: str                 # FK to GoalEvaluation -- never to raw GoalEvidence (GOAL-8); UNIQUE (GOAL-13)
    created_at: datetime
    relevant_to_trust: bool
    direction: GoalAccountabilityDirection
    rationale: str

    review_outcome: GoalAccountabilityReviewOutcome
    negotiation_reason: str | None     # REQUIRED if review_outcome == NEGOTIATE
```

`review_outcome`/`negotiation_reason` is the concrete, explicit signal that
opens a negotiation (Section 7) -- not an inferred "conflict" computed
by comparing two independent judgments algorithmically. The Keyholder
states, in its own assessment, whether the Coach's proposed handling
looks adequate given what Accountability sees; if not, it says so
directly, with a reason.

### 6.2 Resolved: `GoalAccountabilityAssessment` Stays in Goal Management

Second-round review raised this as the one remaining place where a
non-`Goal` concept might still live inside Goal Management. It is now
resolved, on different grounds than first proposed here.

**The deciding question is not "which module has less coupling" — it is
"what is a `GoalAccountabilityAssessment` an assessment *of*?"** The
answer is a `GoalEvaluation`. It interprets a Goal-domain artifact, in
Goal-domain terms (it can reference `GoalNegotiation`, propose
adaptation or replacement, and open a negotiation), regardless of
whether it ever touches Trust at all. A `GoalAccountabilityAssessment`
with `direction=NEUTRAL, relevant_to_trust=False, review_outcome=ACCEPT`
is still a complete, valuable record — the Trust Manager need never
know it exists. That alone establishes it as Goal-domain content, not
Trust-domain content that happens to be produced elsewhere.

This also sharpens what the Trust Manager actually is. It is not "the
module that decides accountability" — accountability is a judgment the
Keyholder makes about a `GoalEvaluation`, and that judgment can stand on
its own with no Trust consequence whatsoever. The Trust Manager is the
module that **manages Trust's history and computation** — it consumes
a completed judgment when one is relevant to it, exactly the same way
it consumes a `CONFIRMED` `Incident` it did not itself decide to
confirm.

A secondary, structural reason reinforces the same conclusion:
`review_outcome=NEGOTIATE` directly drives `GoalNegotiation` (Section
7) — a Goal Management concept. Relocating the assessment to the Trust
Manager would require the Trust Manager to reach back into Goal
Management to open a negotiation, the same kind of cross-module
write-back this document exists to eliminate in the opposite direction
(Section 11.1, fix for Point 3).

The resulting dependency stays strictly one-directional and free of any
cycle:

```
GoalEvidence -> GoalEvaluation -> GoalAccountabilityAssessment -> (event) -> TrustEvidence
```

Goal Management owns the first three; the Trust Manager owns only the
last, reading the third as input it never modifies.

---

## 7. Negotiation -- Multiple Rounds (Fix for Point 5)

### 7.1 Why a Single Exchange Was Not Enough

`philosophy.md` 2.9 describes negotiation as continuing "until they
reach a jointly supported proposal" -- the v1 `GoalNegotiationRecord`
modeled exactly one exchange (one Keyholder concern, one Coach
response, one outcome), which cannot represent a real back-and-forth,
and which forced a `final_intervention` to exist even when
`ESCALATED_TO_USER` meant nothing had actually been resolved.

```python
class GoalNegotiationStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    ESCALATED_TO_USER = "escalated_to_user"
    MOOT = "moot"
    """
    Fix for System State Machine Finding 3: the underlying Goal left
    ACTIVE/PAUSED (COMPLETED/ABANDONED/REPLACED) while this negotiation
    was still OPEN. The negotiation is closed automatically, not left
    orphaned -- it never reaches a decision, because there is nothing
    left to decide (see 9.1, GOAL-14).
    """


@dataclass
class GoalNegotiation:
    """
    MUTABLE -- the same pattern as penalty_windows.status. Created only
    when a GoalAccountabilityAssessment sets
    review_outcome=NEGOTIATE (6.1).
    """
    id: str
    evaluation_id: str
    accountability_assessment_id: str
    status: GoalNegotiationStatus
    final_intervention: GoalInterventionType | None   # populated ONLY when status=RESOLVED (GOAL-12)
    explanation_for_user: str | None                    # populated ONLY when status is RESOLVED or ESCALATED_TO_USER
    created_at: datetime
    resolved_at: datetime | None


@dataclass(frozen=True)
class GoalNegotiationRound:
    """
    Append-only. One exchange within an ongoing negotiation. Any number
    of these may accumulate under one GoalNegotiation before it
    resolves (GOAL-12) -- this is what actually supports "negotiate
    until..." rather than a single fixed exchange.
    """
    id: str
    negotiation_id: str
    round_number: int
    keyholder_position: str
    coach_position: str
    created_at: datetime
```

### 7.2 What the Negotiation Can and Cannot Do

Restated as an implementation-facing invariant (GOAL-7, Section 8):
negotiation can only change `GoalNegotiation.final_intervention` --
which Section 5.2 action is actually taken, once resolved. It has **no
path** to create an `Incident` or a `Penalty Window`; no field on
`GoalNegotiation` or `GoalNegotiationRound`, and no function reachable
from either, writes to `incidents` or `penalty_windows`. A Keyholder
suspicion of an actual Rule violation (e.g., dishonest check-in
reporting) is a separate, independently triggered Incident
investigation entirely outside this module.

### 7.3 `ESCALATED_TO_USER`

A safety valve, not a normal path (unchanged reasoning from v1) -- now
correctly modeled without a fabricated `final_intervention`: while
`ESCALATED_TO_USER`, `final_intervention` is `None`, and
`explanation_for_user` presents both unresolved perspectives directly
to the user, consistent with `philosophy.md` 2.6.

---

## 8. Invariants

| # | Source | Invariant |
|---|---|---|
| GOAL-1 | 1, philosophy.md 2.10 | No function in Goal Management ever writes to `penalty_windows`, `freeze_periods`, or `incidents`. |
| GOAL-2 | 4.3, philosophy.md 2.2 | No single `GoalEvidence` row, of any `GoalOutcome`, automatically triggers a `GoalEvaluation`, a Trust effect, or a lifecycle transition. Only an explicit, separate call creates a `GoalEvaluation`. |
| GOAL-3 | 5.1 | Every `GoalEvaluation` has a non-empty `triggering_evidence_ids`. |
| GOAL-4 | 4.1 | `GoalEvidence` is append-only -- no `UPDATE`/`DELETE` path exists. |
| GOAL-5 | 2.2 | `GoalVersion` is append-only; `adaptation_reason` is required beyond version 1. `Goal.status` and `Goal.archived_at` are the only mutable fields in this domain's primary entities. |
| GOAL-6 | 5.3, philosophy.md 2.5, fix for Point 6 | No `GoalVersion` beyond the first, and no terminal lifecycle transition, takes effect without a `GoalChangeProposal` reaching `ACCEPTED`. Acceptance always applies the exact, immutable `GoalChangeProposalContent` recorded at proposal time -- never content reconstructed from context at acceptance time. |
| GOAL-7 | 7.2 | No field or function reachable from `GoalNegotiation`/`GoalNegotiationRound` writes to `incidents` or `penalty_windows`. |
| GOAL-8 | 6.1 | `GoalAccountabilityAssessment.evaluation_id` always references a `GoalEvaluation` -- never raw `GoalEvidence` directly. |
| GOAL-9 | 5.1, 6.1, fix for Point 1 | `GoalEvaluation` (Coach) has no field answering the accountability question (no `relevant_to_trust`, no autonomy-justification judgment, no direction). That judgment exists exclusively on `GoalAccountabilityAssessment` (Keyholder). |
| GOAL-10 | 11, fix for Point 3 | Goal Management never writes `TrustEvidence` or any other Trust Manager table. It emits `goal_accountability_assessment.recorded` for every `GoalAccountabilityAssessment`, regardless of `relevant_to_trust` — it does not pre-filter on the Trust Manager's behalf (2.11, Domain Interpretation); the Trust Manager alone reads the assessment via `get_accountability_assessment()` and decides whether and how to write `TrustEvidence` from it. |
| GOAL-11 | 3.3, fix for Point 4 | `archived_at` may be set only when `Goal.status` is already terminal (`COMPLETED`/`ABANDONED`/`REPLACED`). Setting it never changes `status`, and it has no behavioral effect anywhere in this system. |
| GOAL-12 | 7.1, fix for Point 5 | `GoalNegotiation.final_intervention` is populated only when `status=RESOLVED`; it is always `None` while `OPEN` or when `ESCALATED_TO_USER`. Any number of `GoalNegotiationRound` entries may exist under one `GoalNegotiation` before it resolves. |
| GOAL-13 | 6.1, second review, Point 2 | At most one `GoalAccountabilityAssessment` may exist per `GoalEvaluation` (`UNIQUE(evaluation_id)`). A revised accountability judgment requires a new `GoalEvaluation`, never a second assessment layered onto an existing one. |
| GOAL-14 | 9.1, fix for System State Machine Finding 3 | Whenever a Goal's lifecycle transitions to `COMPLETED`/`ABANDONED`/`REPLACED`, any `OPEN` `GoalNegotiation` referencing a `GoalEvaluation` of that Goal transitions to `MOOT` in the same transaction — never left `OPEN` indefinitely, and never silently treated as `RESOLVED` (no decision was actually reached). `final_intervention` remains `None` for a `MOOT` negotiation, the same as for `OPEN`/`ESCALATED_TO_USER`. |

---

## 9. Persistence, Transaction Boundaries, and Recovery

### 9.1 Transaction Pattern

```python
def _record_goal_evidence(db, evidence, event) -> None: ...              # append-only insert + event
def _record_evaluation(db, evaluation, event) -> None: ...                # append-only insert + event
def _record_accountability_assessment(db, assessment, event) -> None: ... # append-only insert + event; if review_outcome=NEGOTIATE, ALSO creates the GoalNegotiation in the same transaction
def _add_negotiation_round(db, round_, event) -> None: ...                # append-only insert + event
def _resolve_negotiation(db, negotiation_id, outcome_fields, event) -> None: ...  # GoalNegotiation status update + event
def _apply_goal_lifecycle_transition(db, goal_group_id, new_status, event) -> None:
    """
    Goal.status update + event, in one transaction. When new_status is
    COMPLETED/ABANDONED/REPLACED, this SAME transaction also finds any
    OPEN GoalNegotiation referencing a GoalEvaluation under this
    goal_group_id and transitions each to MOOT, with its own event
    (fix for System State Machine Finding 3, GOAL-14) -- a negotiation
    is never left open once its subject is no longer ACTIVE/PAUSED.
    """
def _apply_goal_adaptation(db, new_version, event) -> None: ...           # GoalVersion insert + Goal.current_version_id update + event
def _archive_goal(db, goal_group_id, now, event) -> None: ...             # Goal.archived_at update + event, guarded by GOAL-11
```

### 9.2 Why This Module Needs Only Minimal Crash Recovery

The only non-terminal, multi-step state this module owns is
`GoalChangeProposal.status=PENDING` and `GoalNegotiation.status=OPEN`.
Everything else is written as a single, immediate, atomic operation with
no intermediate waiting state.

`GoalNegotiation.OPEN` needs no automatic timeout-driven recovery
analogous to `GoalChangeProposal`'s `EXPIRED` path -- a negotiation
between Coach and Keyholder is an internal process with no user-facing
deadline; it simply remains `OPEN`, available for further
`GoalNegotiationRound` entries, until it resolves or is escalated. A
crash mid-negotiation leaves it `OPEN`, which is already its correct,
stable resting state -- nothing needs reconciling.

### 9.3 Startup Reconciliation

```python
def recover_goal_management_state(db: Database, now: datetime) -> None:
    """
    Called from on_system_startup() (system_state_machine.md Section 7,
    the authoritative sequence following the System State Machine
    integration audit, Finding 4), inside the same
    system_startup_lease as every other module's recovery step.
    """
    for proposal in db.get_pending_goal_change_proposals():
        if proposal.proposal_expires_at <= now:
            _transition_proposal(db, proposal.id, GoalProposalStatus.EXPIRED)
            _write_event(db, _proposal_expired_event(proposal))
        # else: leave it PENDING, waiting for the user's response.
    # GoalNegotiation.OPEN requires no reconciliation -- see 9.2.
```

---

## 10. Test Matrix

| # | Scenario | Given | When | Then |
|---|---|---|---|---|
| GT1 | No single period triggers anything, in either direction | a new `GoalEvidence(outcome=MISSED)` and, separately, a new `GoalEvidence(outcome=MET)` | each write completes | neither creates a `GoalEvaluation`, a Trust effect, or a lifecycle change automatically (GOAL-2) |
| GT2 | An evaluation requires triggering evidence | -- | attempt to create a `GoalEvaluation` with empty `triggering_evidence_ids` | rejected (GOAL-3) |
| GT3 | Adaptation creates a new version, not an edit | `GoalVersion(version=1)` exists | `adapt()` via an accepted `GoalChangeProposal` | a new `GoalVersion(version=2, supersedes_id=<v1>)`; v1 unchanged and still readable (GOAL-5) |
| GT4 | Acceptance applies the recorded content, not re-derived content | a `GoalChangeProposal` with a specific `GoalChangeProposalContent` | context changes (e.g., a new conversation) before the user accepts | acceptance still applies exactly the originally recorded `GoalChangeProposalContent` (GOAL-6) |
| GT5 | A declined or expired proposal has no effect | `GoalChangeProposal` | user declines, or it expires | no `GoalVersion`/lifecycle change either way (GOAL-6) |
| GT6 | Coach's evaluation carries no accountability field | any `GoalEvaluation` | inspect its schema/fields | no `relevant_to_trust`, no direction, no autonomy judgment present (GOAL-9) |
| GT7 | Accountability assessment always references an evaluation | -- | attempt to create a `GoalAccountabilityAssessment` without a valid `evaluation_id` | rejected (GOAL-8) |
| GT8 | A NEGOTIATE review outcome opens a negotiation | a `GoalAccountabilityAssessment` with `review_outcome=NEGOTIATE` | it is recorded | a `GoalNegotiation(status=OPEN)` is created in the same transaction (7.1) |
| GT9 | Negotiation supports multiple rounds | an `OPEN` `GoalNegotiation` | several `GoalNegotiationRound` entries are added over time | all rounds are readable in order; `status` remains `OPEN` until explicitly resolved (GOAL-12) |
| GT10 | `final_intervention` is never set while unresolved | an `OPEN` or `ESCALATED_TO_USER` `GoalNegotiation` | inspect `final_intervention` | always `None` (GOAL-12) |
| GT11 | Negotiation cannot create an Incident | a `GoalNegotiation`/`GoalNegotiationRound`, any content | inspect all resulting writes | no row in `incidents`; no row in `penalty_windows` (GOAL-7) |
| GT12 | Goal Management never writes any Trust Manager table | a full evidence -> evaluation -> accountability assessment -> negotiation -> adaptation cycle | inspect all writes | zero writes to `penalty_windows`, `freeze_periods`, `incidents`, or ANY Trust Manager table, including `TrustEvidence` (GOAL-1, GOAL-10) |
| GT13 | Archiving requires a terminal status | a Goal with `status=ACTIVE` | `archive_goal()` is attempted | rejected (GOAL-11) |
| GT14 | Archiving preserves the true terminal outcome | a Goal with `status=ABANDONED` | `archive_goal()` | `archived_at` is set; `status` remains `ABANDONED`, still distinguishable from `COMPLETED`/`REPLACED` (GOAL-11) |
| GT15 | `GOAL_PROGRESS` has a real source | a `GoalEvaluation` triggered by `MET` evidence, with an accompanying `GoalAccountabilityAssessment(direction=PROGRESS, relevant_to_trust=True)` | the Trust Manager extension (Section 11) processes it | a `TrustEvidence(evidence_type=GOAL_PROGRESS)` is produced -- demonstrating the pipeline v1 lacked (Point 2) |
| GT16 | Repeated startup recovery is idempotent | multiple `PENDING` proposals, some expired, some not; an `OPEN` negotiation | `recover_goal_management_state()` run 2x or 10x in a row | the same result as a single run |
| GT17 | At most one assessment per evaluation | a `GoalEvaluation` with an existing `GoalAccountabilityAssessment` | attempt to create a second `GoalAccountabilityAssessment` for the same `evaluation_id` | rejected — `UNIQUE(evaluation_id)`; a new judgment requires a new `GoalEvaluation` instead (GOAL-13) |
| GT18 | A negotiation becomes moot when its Goal is abandoned mid-negotiation | an `OPEN` `GoalNegotiation` on Goal X | Goal X transitions to `ABANDONED` via a separately accepted `GoalChangeProposal` | the negotiation transitions to `MOOT` in the same transaction; `final_intervention` remains `None`; no orphaned `OPEN` negotiation results (GOAL-14, resolves System State Machine Finding 3 / SST6) |

---

## 11. Trust Manager Integration (Read-Only From Goal Management's Side)

### 11.1 Goal Management Publishes; the Trust Manager Owns the Write (Fix for Point 3) — Applied

The v1 design had Goal Management write `TrustEvidence` directly -- this
blurred the same module boundary this system has otherwise enforced
strictly everywhere else (Activity Authorization never writes
`penalty_windows`; Hygiene Privilege never writes `incidents`). The
corrected shape, **now applied on both sides**
(`trust_manager_technical_design.md` Section 15):

```
Goal Management
    -- publishes goal_accountability_assessment.recorded
       referencing a completed GoalAccountabilityAssessment
Trust Manager
    -- consumes the event
    -- reads the assessment via get_accountability_assessment() (below)
    -- decides, under its own rules, whether and how to record evidence
    -- writes TrustEvidence itself
```

Goal Management's only output toward Trust is this event -- nothing in
this document writes to any Trust Manager table (GOAL-10).

```python
def get_accountability_assessment(db: Database, assessment_id: str) -> GoalAccountabilityAssessment | None:
    """
    The ONLY permitted way for the Trust Manager (or any future
    consumer) to read a GoalAccountabilityAssessment -- the same narrow,
    named-function discipline used for get_authorization_freeze_state()/
    get_penalty_window_relevant_domains() elsewhere in this system.
    Exposes the full assessment (relevant_to_trust, direction,
    rationale, trust_domain via its GoalEvaluation's Goal) -- nothing
    about GoalNegotiation, GoalEvaluation.findings, or any other Goal
    Management internals beyond what this one record already contains.
    """
```

`trust_manager_technical_design.md` Section 15 applies this API as
follows:

```python
# Applied in trust_manager_technical_design.md Section 15 -- summarized here for reference.

class EvidenceType(StrEnum):
    INCIDENT_IMPACT = "incident_impact"
    RECOVERY_PROGRESS = "recovery_progress"
    SUSTAINED_PERIOD = "sustained_period"
    MANUAL_REVIEW = "manual_review"
    GOAL_PROGRESS = "goal_progress"
    GOAL_SETBACK = "goal_setback"


def record_goal_assessment_evidence(db: Database, assessment_id: str, event: DomainEvent) -> TrustEvidence | None:
    """
    Lives in the Trust Manager, not here. Reads the referenced
    GoalAccountabilityAssessment via get_accountability_assessment()
    above and, ONLY if relevant_to_trust=True AND direction is PROGRESS
    or SETBACK, writes a TrustEvidence row. direction=NEUTRAL, or
    relevant_to_trust=False, writes nothing and returns None.
    """
```

`direction=NEUTRAL` never produces evidence -- it represents "the
Keyholder reviewed this and judged it not meaningfully indicative
either way," which is itself useful to have on record (on the
assessment) without needing a corresponding Trust effect.

### 11.2 Why This Still Traces Back to a Confirmed Judgment, Never Raw Evidence

`GOAL_SETBACK`/`GOAL_PROGRESS` trace to a
`GoalAccountabilityAssessment`, which itself always references a
`GoalEvaluation` (GOAL-8), which itself always has non-empty triggering
evidence (GOAL-3). This mirrors exactly how `INCIDENT_IMPACT` traces
back to a `CONFIRMED` `Incident`, never an `UNCONFIRMED` one -- the same
confirmation-gating discipline, expressed here through the
evidence -> evaluation -> accountability assessment chain rather than
through `IncidentConfirmation`, because Goals have no equivalent notion
of "confirmation" (there is nothing to confirm; a Goal Outcome is never
in dispute the way an Incident's facts can be).

### 11.3 What Does Not Change in the Trust Manager

This integration does not change what the Trust Manager fundamentally
is: a module that manages Trust's history and computation, not one
that decides accountability. It consumes a `GoalAccountabilityAssessment`
it did not itself produce, exactly the way it already consumes a
`CONFIRMED` `Incident` it did not itself confirm — the judgment always
happens elsewhere, in the domain the judgment is about.

Everything else -- `TrustDomain`, `TrustDomainState`, the recalculation
mechanism, `MAX_ABSOLUTE_DELTA_PER_RECALCULATION`, confidence
computation -- is unaffected. `GOAL_PROGRESS`/`GOAL_SETBACK` evidence
would flow through the exact same `TrustEvidence` ->
`TrustRecalculationEvidence` -> `TrustRecalculation` pipeline as
`INCIDENT_IMPACT`, with the same append-only, confidence-weighted,
capped-delta discipline.

---

## 12. Open Questions Before Implementation

1. **The check-in/observation mechanism** that produces `GoalEvidence`
   (Section 4.2) -- deliberately out of scope, as in v1.
2. **`GoalChangeProposal`/`GoalAccountabilityAssessment` timing
   parameters** (validity windows, etc.) -- not fixed here; your call,
   as in v1.
3. **What triggers Keyholder review of a given `GoalEvaluation`** at
   all (Section 6.1) -- left intentionally open. A periodic policy,
   specific `proposed_intervention` values (e.g., always reviewing
   `PROPOSE_ABANDONMENT`/`PROPOSE_REPLACEMENT`), or a pattern across
   multiple evaluations are all plausible; none is chosen here.
4. **Whether a Goal's `trust_domain` may ever change** -- currently
   fixed at creation (Section 11.1) and absent from
   `GoalChangeProposalContent` (Section 5.3). If a real need arises to
   move a Goal between domains, that is closer in weight to a
   replacement (2.4) than an adaptation, but is not designed here.
5. **How `relevant_to_trust`/`direction` interact with a history of
   repeated assessments for the same Goal** -- deliberately not
   automated (mirrors the reasoning already given for
   `GoalEvaluation` in v1): each `GoalAccountabilityAssessment` stands
   on the Keyholder's own judgment with full visibility into history,
   not a formula. If a pattern of Keyholder judgment itself looks
   questionable over time, that is a matter for human audit
   (Observations), not a rule this document should encode.

---

## 13. Domain Events (Consolidated)

Individual events were introduced throughout this document's transition
tables (Sections 3, 6, 7); this section consolidates them in one place,
plus the one event added for the Trust Manager integration (Section 11)
— appended here, rather than inserted earlier, so that no existing
section's number changes (several other documents already cross-
reference this document's Sections 9 and 11).

| event_type | source_module | When It Occurs |
|---|---|---|
| `goal.created` | goal_management | a new `Goal` + `GoalVersion(version=1)` |
| `goal.adapted` | goal_management | a new `GoalVersion` under an existing `goal_group_id` |
| `goal.paused` / `goal.resumed` | goal_management | the corresponding lifecycle transition (3.2) |
| `goal.completed` / `goal.abandoned` / `goal.replaced` | goal_management | the corresponding terminal transition (3.2) |
| `goal_evidence.recorded` | goal_management | any new `GoalEvidence` row |
| `goal_evaluation.recorded` | goal_management | any new `GoalEvaluation` |
| `goal_change_proposal.created` | goal_management | any new `GoalChangeProposal` |
| `goal_change_proposal.resolved` | goal_management | `ACCEPTED` / `DECLINED` / `EXPIRED` |
| `goal_accountability_assessment.recorded` | goal_management | any new `GoalAccountabilityAssessment` — consumed by the Trust Manager (Section 11.1; `trust_manager_technical_design.md` Section 15), **regardless of `relevant_to_trust`** (GOAL-10) |
| `goal_negotiation.opened` | goal_management | a new `GoalNegotiation(status=OPEN)`, triggered by `review_outcome=NEGOTIATE` (6.1) |
| `goal_negotiation.round_added` | goal_management | any new `GoalNegotiationRound` |
| `goal_negotiation.resolved` / `goal_negotiation.escalated` / `goal_negotiation.moot` | goal_management | the corresponding `GoalNegotiation` terminal transition (7.1, GOAL-14) |

All events use the transactional outbox already defined in
`penalty_window_technical_design.md` — no new mechanism here.

# Activity Authorization — Technical Design (v5)

> **v5 — fix from the `system_state_machine.md` integration audit
> (Finding 4):** Section 16.2 no longer defines `on_process_startup()`
> or the startup lease mechanism. Startup orchestration was never truly
> this module's responsibility — it belongs to the runtime/bootstrap
> layer, now formalized in `system_state_machine.md` Section 7 as
> `on_system_startup()`. This document retains only
> `recover_activity_authorization_state()` (16.3), called from that
> authoritative sequence.
>
> Status: **Architecture baseline — approved for implementation.**
> Reached this status once crash/restart recovery (v3), the API-boundary
> fixes (v4), and the startup-orchestration relocation to the
> runtime/bootstrap layer (v5) were all applied — this document is now
> the baseline for Activity Authorization's implementation, not a
> proposal still awaiting changes.
>
> Based on `philosophy.md` v1.5
> Section 4, and builds on `penalty_window_technical_design.md` (the
> freeze mechanism extended with `reason='partnered_intimacy_authorization'`,
> `expires_at` and `end_reason` as the single source of truth,
> `domain_events` as a transactional outbox with a claim mechanism, the
> public read API `get_authorization_freeze_state()` — invariants
> I21–I24) and `trust_manager_technical_design.md` (the same
> append-only-evidence pattern plus separated transaction boundaries).
>
> This document also formalizes the Token Ledger as part of its scope.
>
> **v2 — fixes from the first review:** (1) `maximum_unlock_duration` in
> `ActivityPolicy`; (2) request/decision persisted immediately,
> `AuthorizationLifecycleStatus`; (3) idempotency via explicit IDs; (4)
> a final balance check under a per-user lock in the commit; (5)
> `PENDING_FREEZE` as an explicit "not yet unlocked" state; (6)
> `ActivityAuthorizationSession`; (7) `applies_during_penalty_window`
> based on the window's existence, not FROZEN/ACTIVE.
>
> **v3 — fixes from the second review:** (8) a symmetric `PENDING_RESUME`
> on closure; (9) revalidation of the Penalty Window context and a
> pinned policy inside the commit; (10) a unified `expires_at`; (11) a
> transactional outbox; (12) `DENIED` as a full-fledged terminal state.
> Plus a new Section 16 (crash/restart recovery): absolute timestamps,
> startup reconciliation, RT1–RT10.
>
> **v4 — fixes from the third review (implementation resilience of
> recovery):** (13) recovery never reads another module's tables
> directly — introduced the public
> `penalty_engine.get_authorization_freeze_state()` API
> (penalty_window_technical_design.md 2.5);
> `find_uncommitted_freeze_requests()` removed as redundant and
> boundary-violating; (14) startup is a single-writer operation
> protected by a database `system_startup_lease` (16.2); (15) the outbox
> is extended with safe claim/retry behavior for multiple publishers
> (`claimed_at`/`claim_expires_at`, Penalty Window document 4.6) and
> explicit at-least-once semantics with a consumer-side dedup layer; (16)
> a single injected `Clock` with protection against system clock drift
> (16.7). The test matrix is extended with RT11–RT15.

---

## 1. Module Scope and Boundaries

Activity Authorization answers the question: *may the user perform a
specific, previously agreed-upon activity right now?* It is a third,
separate decision layer alongside the Trust Manager (safe autonomy) and
the Penalty Engine (the duration of a temporary restriction):

```
Trust Manager           -> how safe is it to grant autonomy (context)
Penalty Engine           -> what temporary restriction currently applies (duration, freeze)
Activity Authorization -> may this specific thing happen RIGHT NOW (decision + tokens)
```

Dependencies are one-directional and follow the same pattern established
for the previous two modules:

- Activity Authorization **reads** the existence and status
  (`COMPLETED` versus anything else) of the active Penalty Window — not
  its `FROZEN`/`ACTIVE` distinction, see 3.1 — but **never writes** to
  `penalty_windows` or `freeze_periods` directly (see the Penalty
  Window document, Section 2.3, the addition on the boundary of
  responsibility).
- Activity Authorization **neither reads nor writes** anything from the
  Trust Manager — authorizing a specific activity does not depend on
  the Trust score in any domain (that would be the same kind of
  structural isolation violation as with `assess_severity()`/
  `should_extend()`).
- Activity Authorization **never generates** `Trust Evidence` or an
  `Incident` as a direct side effect of a routine authorization or
  denial (`philosophy.md` 4.3) — only as a consequence of confirmed
  behavior outside the authorized scope, established out of band (see
  11).

Outside the scope of this document: the concrete list of all regulated
activities beyond the illustrative examples from review, the content/
prompting of the `UrgeSupportProtocol` (a separate follow-up design,
much like "who populates `IncidentEvidence`" in the Trust Manager), and
`should_extend()`.

---

## 2. Activity Taxonomy

```python
class ActivityCategory(StrEnum):
    PROHIBITED = "prohibited"              # never authorizable, by any path
    TOKEN_GATED = "token_gated"             # a normal token-gated activity
    TOKEN_GATED_RESTRICTABLE = "token_gated_restrictable"  # token_gated, but
                                             # may be fully prohibited during a PW (not just more expensive)
```

```python
@dataclass(frozen=True)
class ActivityDefinition:
    activity_type: str                      # 'pornography' | 'solo_release' | 'partnered_intimacy' | ...
    category: ActivityCategory
    display_name: str
    created_via_consent_id: str             # critical_change - the same pattern as TrustDomain
```

The `ActivityDefinition` registry is configurable, the same as Trust
domains — a new activity, or a change to an existing activity's
category, is always a `critical_change` (`philosophy.md` 4.5); the
runtime may not introduce one on its own.

Three illustrative activities from review, as the initial content of the
registry (not an exhaustive list, just a demonstration of the data
shape):

| `activity_type` | `category` |
|---|---|
| `pornography` | `PROHIBITED` |
| `solo_release` | `TOKEN_GATED_RESTRICTABLE` |
| `partnered_intimacy` | `TOKEN_GATED` |

---

## 3. Policy Model

Every `TOKEN_GATED`/`TOKEN_GATED_RESTRICTABLE` activity has **two policy
variants** — outside a Penalty Window and during one — as separate,
explicitly versioned configurations (a `critical_change` parameter, the
same pattern as `Rule` versioning in the main schema):

```python
@dataclass(frozen=True)
class ActivityPolicy:
    id: str
    activity_type: str
    applies_during_penalty_window: bool     # see 3.1 - the definition does NOT depend on the FROZEN state

    token_cost: int
    minimum_balance: int                    # the lowest balance the spend is allowed to reach
    explicit_confirmation_required_for_debt: bool
    prohibited_while_window_active: bool    # True overrides token_cost/minimum_balance entirely
    freezes_penalty_window: bool            # True only for partnered_intimacy-like activities
    maximum_unlock_duration: timedelta | None  # a REQUIRED rule, see below

    created_via_consent_id: str
    version: int
    supersedes_id: str | None = None        # the same versioning pattern as Rule
```

**Invariant for `maximum_unlock_duration`** (missing before, added per
review):

```
POLICY-DURATION-1
  If freezes_penalty_window == False, then maximum_unlock_duration MUST be None
  (the activity does not freeze the PW, so no maximum unlock duration makes sense).

POLICY-DURATION-2
  If freezes_penalty_window == True, then maximum_unlock_duration MUST be a
  positive, non-zero value (a safety net against an unbounded freeze,
  see philosophy.md 3.5, the spirit of the principle — even here, a
  temporary pause must have an explicit upper bound).

POLICY-DURATION-3
  maximum_unlock_duration is a critical_change parameter (AP6/AP7) — the runtime
  may not change it outside the consent flow, same as token_cost/minimum_balance.
```

### 3.1 What `applies_during_penalty_window` Actually Depends On

The policy variant selected (outside PW vs. during PW) depends
exclusively on whether **an active (non-completed) Penalty Window
exists** — not on whether that window is currently effectively
`FROZEN`:

```
applies_during_penalty_window =
    an active Penalty Window exists AND its status != COMPLETED

NOT:
    penalty_window.status == ACTIVE   (this would incorrectly exclude FROZEN)
```

A frozen Penalty Window is still a Penalty Window — a Freeze pauses its
clock, but does not turn it into normal operation. This has a concrete
practical consequence: if a window is frozen due to a
`temporary_wear_exemption` (e.g., vacation) and the user requests
partnered intimacy, the "during PW" price still applies — not the
"outside PW" price — even though the window is not actually counting
down at that moment. Section 1 (module scope) is adjusted accordingly
above.

The concrete values from the requirements (`solo_release` outside PW:
`token_cost=1, minimum_balance=0`; `partnered_intimacy` during PW:
`token_cost=2, minimum_balance=-2, freezes_penalty_window=True`) are
instances of this structure, not code — which directly fulfills
AP6/AP7 (the runtime may not change prices, debt limits, or the maximum
unlock duration on its own).

`PROHIBITED` activities **have no** `ActivityPolicy` at all — there is
no way to authorize them, so no configurable parameter exists for them
that could (even by mistake) be loosened.

---

## 4. Authorization Request and Decision

### 4.0 Idempotency Through Command Identity, Not Heuristics (Fix for Point 3)

The client/Conversation Layer generates a stable `request_id` on first
receipt of a request, and a `confirmation_command_id` on first receipt
of a confirm/decline action. On any retry (double-click, network
retry), the **same ID is reused**, never a new one. The database
enforces this identity directly:

```
UNIQUE(activity_authorization_requests.request_id)
UNIQUE(activity_authorization_confirmation_commands.confirmation_command_id)
UNIQUE(token_ledger_entries.authorization_decision_id) WHERE entry_type = 'SPEND'
```

None of these identities is derived from a rounded timestamp or the
serialization of free-form context — that was the original design
(`compute_idempotency_key()` based on timestamp/context), which review
correctly flagged as unreliable. It is replaced entirely, not adjusted.

```python
class ConfirmationAction(StrEnum):
    CONFIRM = "confirm"
    DECLINE = "decline"

@dataclass(frozen=True)
class ActivityAuthorizationConfirmationCommand:
    confirmation_command_id: str    # generated by the client, UNIQUE, stable across retries
    decision_id: str                # FK to an ActivityAuthorizationDecision in PENDING_CONFIRMATION
    source_message_id: str | None
    action: ConfirmationAction
    created_at: datetime
```

```python
class ActivityRequestSource(StrEnum):
    USER_REQUEST = "user_request"
    URGE_DISCLOSURE = "urge_disclosure"

@dataclass(frozen=True)
class ActivityAuthorizationRequest:
    request_id: str                         # generated by the client, UNIQUE, stable across retries
    created_at: datetime
    activity_type: str
    source: ActivityRequestSource
    active_penalty_window_id: str | None    # the existence of a non-completed window, see 3.1 — read, never written by this module
    source_message_id: str | None           # e.g., a Discord message ID, for the audit trail
    context: dict                            # free-form, for audit/Coach context only — NEVER an input to idempotency
```

**The request is persisted immediately on receipt**, regardless of how
the decision turns out — this is the fix for Point 2 (a conflict with
the audit model), see 4.2 below.

`ActivityRequestSource.URGE_DISCLOSURE` is type-separated from
`USER_REQUEST` — admitting a desire never travels the same path as a
request for authorization (see `philosophy.md` 4.3). `URGE_DISCLOSURE`
leads to the `UrgeSupportProtocol`, never to an
`ActivityAuthorizationDecision` at all — a desire by itself has no
`decision`, because nothing is being decided.

### 4.1 Decision and Lifecycle (Fixes for Points 2, 5, 8, 9, 10, 12)

```python
class AuthorizationDecisionType(StrEnum):
    ALLOW_WITH_TOKEN = "allow_with_token"
    ALLOW_WITH_LIMITED_DEBT = "allow_with_limited_debt"
    DENY_INSUFFICIENT_TOKENS = "deny_insufficient_tokens"
    DENY_PROHIBITED_ACTIVITY = "deny_prohibited_activity"
    DENY_ACTIVE_RESTRICTION = "deny_active_restriction"

class AuthorizationLifecycleStatus(StrEnum):
    DENIED = "denied"                                # a DENY_* decision — TERMINAL, fix for Point 12
    PENDING_CONFIRMATION = "pending_confirmation"     # waiting on the user (debt)
    DECLINED = "declined"                             # the user declined — TERMINAL
    EXPIRED = "expired"                               # confirmation did not arrive in time — TERMINAL
    PENDING_COMMIT = "pending_commit"                 # ready for the token write
    PENDING_FREEZE = "pending_freeze"                 # tokens written, awaiting Penalty Engine confirmation
    ACTIVE = "active"                                  # confirmed as unlocked
    PENDING_RESUME = "pending_resume"                  # closure initiated, awaiting Penalty Engine confirmation (fix for Point 8)
    CLOSED = "closed"                                  # the entire cross-module lifecycle is genuinely complete — TERMINAL
    FAILED = "failed"                                  # the freeze could not be confirmed within the limit — TERMINAL, with compensation
```

Every `ActivityAuthorizationDecision` **always has exactly one**
`lifecycle_status` — no special "`None` for DENY_*" case (fix for Point
12). A `DENY_*` decision is created directly with
`lifecycle_status=DENIED`, a terminal state of the same nature as
`DECLINED`/`EXPIRED`/`CLOSED`/`FAILED`. This simplifies every future
check of "is this decision finished?" to a single condition
(`lifecycle_status in TERMINAL_STATUSES`), with no need to special-case
`None`.

```python
@dataclass
class ActivityAuthorizationDecision:
    """
    A MUTABLE row with its own `lifecycle_status` — the same pattern as
    penalty_windows.status. Every transition is atomic with the write
    of the corresponding domain_event (the _apply_transition pattern).
    """
    id: str
    created_at: datetime
    request_id: str
    user_id: str                             # needed for per-user serialization in commit_authorization() (8.2)

    decision: AuthorizationDecisionType
    lifecycle_status: AuthorizationLifecycleStatus
    token_delta: int                         # signed, 0 for DENIED
    resulting_balance: int                   # a PRELIMINARY calculation — see 8.2; the final check happens elsewhere
    reason_code: str
    explanation: str
    requires_user_confirmation: bool
    freeze_penalty_window: bool

    # A pinned policy version (fix for Point 9) — auditability: the
    # decision always references the SPECIFIC version of the rules it
    # was computed under, even if a newer version arises in the
    # meantime via the consent flow.
    policy_id: str

    # Absolute timestamps (fix for "crash/restart recovery," point 1) —
    # NEVER an in-memory timer. The source of truth for "expired/not
    # expired" is always a comparison of this field against `now`,
    # never elapsed process runtime.
    confirmation_expires_at: datetime | None       # only for PENDING_CONFIRMATION
    freeze_confirmation_deadline_at: datetime | None  # only for PENDING_FREEZE (see 8.4)

    # The single source of truth for the maximum unlock duration (fix
    # for Point 10) — a copy of the SAME value as
    # freeze_periods.expires_at, carried over via the domain_event
    # payload at the moment the freeze is confirmed (8.4), NEVER
    # independently recomputed inside this module.
    session_expires_at: datetime | None
```

**`resulting_balance` on this row is a preliminary estimate, not a
binding value** — `authorize_activity()` remains a pure, deterministic
function over the data passed to it, but review correctly pointed out
that time may pass between its call and the actual `SPEND` write
(waiting for debt confirmation), or another concurrent transaction may
occur in the meantime. The final, binding balance check is repeated
inside `commit_authorization()` (see 8.2) — `authorize_activity()`
itself is no longer considered the place where DA1 is conclusively
enforced.

### 4.2 Denial as a Terminal Lifecycle State, Not a Special Case (Fixes for Points 2 and 12)

`DENY_*` decisions are **immediately final** from `authorize_activity()`
— they are created directly with `lifecycle_status=DENIED` (4.1), never
creating a `TokenLedgerEntry` or a freeze request. They remain, however,
fully audit-recorded, exactly like `ALLOW_*` — both the request and the
decision are persisted immediately (4.0), so a "denial" is never, in any
sense, "as if it never happened" (an inaccurate phrasing used in an
earlier version of this document) — **it has no token or freeze effect
functionally, but it is fully traceable for audit purposes**, including
how many times and why it occurred.

Unifying `DENIED` with the other terminal states (`DECLINED`,
`EXPIRED`, `CLOSED`, `FAILED`) into a single set means that any future
code asking "is this decision over?" writes one condition
(`lifecycle_status in TERMINAL_STATUSES`), rather than a special branch
for `None`.

### 4.3 Decision Flow (Deterministic, Not the LLM)

```python
CONFIRMATION_WINDOW = timedelta(minutes=15)   # parameter, configurable

def authorize_activity(request, definition, policy, current_balance_snapshot, now) -> ActivityAuthorizationDecision:
    """
    A purely deterministic, PRELIMINARY function — the same pattern as
    assess_severity() in the Trust Manager, but with an explicit warning
    that its output is not a binding write (see 4.1, 8.2). The LLM may
    interpret user input into ActivityRequestSource/activity_type, but
    NEVER directly into AuthorizationDecisionType.
    """
    if definition.category == ActivityCategory.PROHIBITED:
        return _deny(request, "DENY_PROHIBITED_ACTIVITY", "This category is never authorizable.")

    if policy.prohibited_while_window_active and request.active_penalty_window_id is not None:
        return _deny(request, "DENY_ACTIVE_RESTRICTION", "This activity is prohibited during a Penalty Window.")

    prospective_balance = current_balance_snapshot - policy.token_cost
    if prospective_balance < policy.minimum_balance:
        return _deny(request, "DENY_INSUFFICIENT_TOKENS", f"Insufficient tokens (min. {policy.minimum_balance}).")

    decision_type = (AuthorizationDecisionType.ALLOW_WITH_LIMITED_DEBT
                      if prospective_balance < 0
                      else AuthorizationDecisionType.ALLOW_WITH_TOKEN)
    requires_confirmation = prospective_balance < 0 and policy.explicit_confirmation_required_for_debt
    return ActivityAuthorizationDecision(
        decision=decision_type,
        lifecycle_status=(AuthorizationLifecycleStatus.PENDING_CONFIRMATION
                           if requires_confirmation
                           else AuthorizationLifecycleStatus.PENDING_COMMIT),
        token_delta=-policy.token_cost,
        resulting_balance=prospective_balance,   # PRELIMINARY, see 4.1
        requires_user_confirmation=requires_confirmation,
        freeze_penalty_window=policy.freezes_penalty_window and request.active_penalty_window_id is not None,
        policy_id=policy.id,                     # PINNED — auditability, see 4.1/8.2
        confirmation_expires_at=(now + CONFIRMATION_WINDOW) if requires_confirmation else None,
        freeze_confirmation_deadline_at=None,     # set only at PENDING_FREEZE (8.4), not here
        session_expires_at=None,                  # set only when the freeze is confirmed (8.4) — the single source of truth
        ...
    )

def _deny(request, reason_code, explanation) -> ActivityAuthorizationDecision:
    return ActivityAuthorizationDecision(
        decision=AuthorizationDecisionType[reason_code],
        lifecycle_status=AuthorizationLifecycleStatus.DENIED,   # fix for Point 12 — always an explicit state
        token_delta=0,
        reason_code=reason_code,
        explanation=explanation,
        requires_user_confirmation=False,
        freeze_penalty_window=False,
        confirmation_expires_at=None,
        freeze_confirmation_deadline_at=None,
        session_expires_at=None,
        ...
    )
```

`maximum_unlock_duration` is **no longer copied** into
`ActivityAuthorizationDecision` at the moment of `authorize_activity()`
(unlike in v2) — it stays only on `ActivityPolicy` (3), from where the
Penalty Engine reads it at the moment it creates the `freeze_periods`
row and computes `expires_at` from it **exactly once** (see 8.4, fix for
Point 10). Copying `maximum_unlock_duration` into the decision in v2
would have tempted Activity Authorization to use it for its own,
independent recomputation of `expires_at` — that is exactly what we are
now deliberately avoiding.

`prohibited_while_window_active` is evaluated **before** the balance
check — a prohibition during a PW is a harder condition than tokens,
not an alternative to them (this matches
`solo_release_during_penalty_window` from the requirements).

---

## 5. Token Ledger

### 5.1 Accounting Principle (Per Review Clarification)

```python
class LedgerEntryType(StrEnum):
    EARN = "earn"
    SPEND = "spend"
    ADJUSTMENT = "adjustment"
    REVERSAL = "reversal"

@dataclass(frozen=True)
class TokenLedgerEntry:
    id: str
    user_id: str                            # ready for future multi-user support
    delta: int                               # signed
    entry_type: LedgerEntryType
    reason_code: str
    authorization_decision_id: str | None    # populated for a SPEND arising from an authorization — SEE the UNIQUE constraint (4.0)
    source_event_id: str | None              # a more general link (e.g., a completed task -> EARN)
    reverses_entry_id: str | None            # populated ONLY for REVERSAL
    created_at: datetime
```

```python
def current_balance(db: Database, user_id: str) -> int:
    """balance = the sum of all ledger entries. No other definition of the
    balance exists — see 5.2 for the cache."""
    return db.sum_ledger_deltas(user_id)
```

No separate "debt repayment engine" exists — debt is simply a negative
`balance`, and any further `EARN` automatically reduces it, because
`balance` is a sum, not a state machine with its own priority logic.
Exactly per the review clarification.

### 5.2 A Materialized Balance as a Cache (Optional, Not the Source of Truth)

```python
@dataclass(frozen=True)
class TokenBalanceCache:
    user_id: str
    cached_balance: int
    as_of_entry_id: str                     # the last ledger entry included in the cache
    updated_at: datetime
```

If performance eventually requires a cache (once there are many
entries), it is a purely derived, reconstructible value —
`TokenBalanceCache` may be discarded at any time and recomputed from
`TokenLedgerEntry` without losing information (the same pattern as
`TrustDomainState.score` versus `TrustRecalculation` in the Trust
Manager).

### 5.3 Append-Only, Reversals Via a Compensating Record

The same pattern as `TrustEvidence`/`record_compensating_evidence()`:

```python
def reverse_entry(db: Database, original_entry_id: str, reason: str) -> TokenLedgerEntry:
    """
    The only way to "undo" an EARN/SPEND. Creates a new REVERSAL row with
    the opposite delta, referencing original_entry_id. The original row
    is never edited or deleted.
    """
```

---

## 6. Limited Token Debt

Debt is a property of a specific `ActivityPolicy.minimum_balance`, not a
global account setting — different activities may allow a different
depth of debt (AP5 from the requirements: `solo_release` must never
create debt at all; `partnered_intimacy` during a PW may go down to
`-2`).

```
INVARIANT: no SPEND transaction may create a resulting_balance
lower than what ActivityPolicy.minimum_balance permits FOR THAT
activity in THAT context (PW active/inactive) - see DA1 in Section 10.
```

`explicit_confirmation_required_for_debt` means that if an authorization
would create a negative balance, `ActivityAuthorizationDecision` is
returned with `requires_user_confirmation=True`, and **the ledger entry
is not written until the user provides confirmation** — that is a
separate step (see 8), not part of the initial decision.

---

## 7. Integration With the Penalty Window Freeze Mechanism

As clarified during review: Activity Authorization **never** calls
`freeze()`/`resume()` on `penalty_windows` directly. It only issues
`ActivityAuthorizationDecision.freeze_penalty_window=True` and
`authorization_decision_id`; the Penalty Engine performs its own
transition based on that (see the Penalty Window document, the addition
to Section 2.3, and invariants I21/I22 there).

```
ActivityAuthorization.authorize_activity()
        |
        |  decision.freeze_penalty_window == True
        v
PenaltyEngine.freeze(
    penalty_window_id=...,
    reason='partnered_intimacy_authorization',
    authorization_decision_id=decision.id,
)
```

The maximum unlock duration (`maximum_unlock_duration`) is a property of
`ActivityPolicy` (`critical_change`). The Penalty Engine reads it at the
moment it creates the `freeze_periods` row and computes `expires_at`
from it **exactly once** (`started_at + maximum_unlock_duration`) — this
value is then the single source of truth for the rest of the lifecycle
(see 8.4/8.5), shared between both modules via the event payload, never
independently recomputed. Enforcement (automatic closure once
`expires_at` is reached) is part of the Penalty Engine's
`ensure_current_state()`/startup reconciliation (see
`penalty_window_technical_design.md`, Section 4.5) — already
implemented there, not an open question.

---

## 8. Confirmation and Transaction Flow

### 8.1 A Flow Driven by `lifecycle_status`

```
authorize_activity() returns a PRELIMINARY decision
        │
        ▼
Request AND Decision are persisted IMMEDIATELY (fix for Point 2) ────► the audit trail exists
        │                                                        from this moment, even for DENIED
        ▼
DENY_* ? ──YES──► DENIED (TERMINAL, fix for Point 12 — see 4.2)
        │ NO
        ▼
lifecycle_status = PENDING_CONFIRMATION?  ──NO──► PENDING_COMMIT (directly)
        │ YES
        ▼
The user responds (ActivityAuthorizationConfirmationCommand)
        │
        ├─ CONFIRM  → PENDING_COMMIT
        ├─ DECLINE  → DECLINED (TERMINAL — no token/freeze effect, but audit-recorded)
        └─ (timeout) → EXPIRED (TERMINAL, the same property as DECLINED)
        ▼
PENDING_COMMIT → commit_authorization() (see 8.2, context revalidation + a final concurrency check)
        │
        ├─ freeze_penalty_window == False → CLOSED (no freeze needed, done)
        └─ freeze_penalty_window == True  → PENDING_FREEZE (see 8.4)
                                                   │
                                                   ▼
                                                ACTIVE (confirmed by the Penalty Engine)
                                                   │
                                                   │  end_session() OR automatic expiration
                                                   ▼
                                             PENDING_RESUME (see 8.5 — symmetric with PENDING_FREEZE)
                                                   │
                                                   ▼
                                               CLOSED
```

`DENIED` never enters the rest of this diagram — it is already final the
moment `authorize_activity()` returns it (4.2).

### 8.2 An Atomic Write With a Safe, Final Concurrency Check (Fix for Point 4)

Review correctly flagged a race condition: `authorize_activity()`
computes `prospective_balance` from a balance snapshot passed to it —
another, concurrent authorization over the same balance could occur
between that computation and the actual `SPEND` write. Two requests for
`cost=2` at `balance=2` would both pass the check and together push the
balance to `-2`, even if the policy allowed no debt at all.

Solution: **DA1 is bindingly re-enforced inside `commit_authorization()`,
against the current balance read within the same transaction/lock as the
`SPEND` write** — not just once, earlier, inside the pure
`authorize_activity()` function.

```python
def commit_authorization(db: Database, decision: ActivityAuthorizationDecision, event: DomainEvent, now: datetime) -> ActivityAuthorizationDecision:
    """
    Uses BEGIN IMMEDIATE (or an equivalent RESTART-SAFE per-user write
    serialization — a DB row lock / serializable transaction /
    optimistic CAS with a persisted version, NEVER just an in-memory
    mutex, see Section 16) so that no other concurrent
    commit_authorization() for the SAME user can read the balance
    between the read and the write performed here.
    """
    with db._connect_immediate() as conn:   # BEGIN IMMEDIATE — see the note below
        # Step 1: context revalidation (fix for Point 9) — the policy is
        # read PINNED by decision.policy_id, not by the "currently
        # active" version. Auditability takes precedence: the decision
        # matches the version of the rules in effect at the moment of
        # authorize_activity(), even if a newer version has since
        # arisen via the consent flow.
        policy = _load_policy_by_id(conn, decision.policy_id)

        # But the Penalty Window context must be re-verified, not taken
        # from the snapshot in the request — it may have ended, or newly
        # arisen, in the meantime.
        current_pw_context = _current_penalty_window_context(conn, decision.user_id)
        if decision.freeze_penalty_window and current_pw_context.active_penalty_window_id is None:
            # The assumption the decision was made under (a non-completed
            # window exists) no longer holds — the window ended in the meantime.
            _transition_decision(conn, decision.id, AuthorizationLifecycleStatus.FAILED,
                                  reason_code="CONTEXT_CHANGED_PW_NO_LONGER_ACTIVE")
            _write_event(conn, _decision_failed_event(decision))
            return _reload_decision(conn, decision.id)

        if policy.freezes_penalty_window:
            # A pre-check of I21 (soft check) — saves an unnecessary
            # SPEND, even though final enforcement remains with the
            # Penalty Engine (a partial unique index, see the Penalty
            # Window document).
            if _has_open_intimacy_freeze(conn, current_pw_context.active_penalty_window_id):
                _transition_decision(conn, decision.id, AuthorizationLifecycleStatus.FAILED,
                                      reason_code="CONFLICTING_OPEN_SESSION")
                _write_event(conn, _decision_failed_event(decision))
                return _reload_decision(conn, decision.id)

        # Step 2: the final, binding balance check (fix for Point 4, DA1-CONCURRENT)
        actual_current_balance = _current_balance_locked(conn, decision.user_id)
        actual_prospective_balance = actual_current_balance - policy.token_cost

        if actual_prospective_balance < policy.minimum_balance:
            # The balance changed unfavorably between authorize_activity()
            # and the commit (concurrent spending). A safe failure, NO SPEND.
            _transition_decision(conn, decision.id, AuthorizationLifecycleStatus.FAILED,
                                  reason_code="DA1_RECHECK_FAILED_CONCURRENT_SPEND")
            _write_event(conn, _decision_failed_event(decision))
            return _reload_decision(conn, decision.id)

        _insert_ledger_entry(conn, _decision_to_ledger_entry(decision, actual_prospective_balance))
        next_status = (AuthorizationLifecycleStatus.PENDING_FREEZE
                       if decision.freeze_penalty_window
                       else AuthorizationLifecycleStatus.CLOSED)
        freeze_deadline = (now + FREEZE_CONFIRMATION_TIMEOUT) if decision.freeze_penalty_window else None
        _transition_decision(conn, decision.id, next_status,
                              resulting_balance=actual_prospective_balance,
                              freeze_confirmation_deadline_at=freeze_deadline)   # an absolute timestamp, not an in-memory timer
        _write_event(conn, event)   # activity_authorization.committed
        # NOTE: the INSERT into freeze_periods does NOT happen here (see 8.3) -
        # this transaction belongs exclusively to the Activity Authorization module.
```

`decision.resulting_balance`, written in `authorize_activity()` (4.1),
is therefore only a **display-only estimate for the user at the moment
of the request** — the actual, binding balance is always the one
computed and written by `commit_authorization()` under the lock. If it
differs (a concurrent spend occurred in the meantime),
`commit_authorization()` safely fails into `FAILED`, rather than
silently writing an inconsistent state.

**Note on the locking implementation:** `db._connect_immediate()` is an
illustrative name for "any mechanism that guarantees reading the
balance and writing the `SPEND` for a given `user_id` are isolated from
concurrent occurrences of the same operation" — the concrete choice
(SQLite `BEGIN IMMEDIATE`, an application-level per-user mutex, an
optimistic version on `TokenBalanceCache`) is an implementation detail
to be finalized when the code is written. **It must, however, be a
restart-safe (database-backed) mechanism, not merely an in-memory
mutex** — an in-memory lock would disappear after a process restart and
protect nothing (see Section 16, `RESTART-LOCK-1`). The invariant that
must hold regardless of the specific mechanism is DA1-CONCURRENT (see
Section 10).

### 8.3 The Cross-Module Boundary: An Open Question About Atomicity

Because Activity Authorization must not write to `penalty_windows`
(Sections 1, 7), it remains an **open question** exactly how to fulfill
the review's requirement that "AuthorizationDecision + TokenLedgerEntry
+ FreezePeriod + audit events are created atomically" — and this is
deliberately not resolved here with a single solution.

**With a purely event-driven architecture**, full atomicity between
`AuthorizationDecision`, the Token Ledger, and `FreezePeriod` is not
achievable. In such an arrangement, the flow would look like this:

1. `commit_authorization()` (8.2) — an atomic write of the decision plus
   the ledger, within the Activity Authorization module's own
   transaction. It emits `activity_authorization.committed` with
   `freeze_penalty_window`/`authorization_decision_id` in the payload.
2. The Penalty Engine consumes this event (the same idempotency pattern
   used elsewhere in the system) and **only then** calls its own
   `freeze()`, within its own transaction (Penalty Window document,
   Section 3.1).

Between steps 1 and 2, this model would have a short window where tokens
have been deducted but the `freeze_periods` row does not yet exist — a
real, though traceable and modelable, risk (see Section 11).

**If the system were to use transactional orchestration** in the future
(a Transaction Coordinator / Unit of Work) across the individual
modules, it might be possible to perform this operation atomically
while preserving each module's ownership — the Penalty Engine would
remain the sole owner of writes to `freeze_periods`, but the
orchestrator would coordinate a single transaction spanning both
modules, without breaching the boundary of responsibility (Activity
Authorization would still not "own" the table; only its write and the
Penalty Engine's write would be committed together under external
coordination).

This document therefore proposes the event-driven variant (8.2, above)
as **a functional solution for the first implementation**, not as an
architectural conclusion that full atomicity is generally unachievable.
The definitive choice between the event-driven approach and future
transactional orchestration remains open — see Section 14 and the note
on the Decision Orchestrator at the end of the document.

### 8.4 Saga Semantics: `PENDING_FREEZE` → `ACTIVE` (Fixes for Points 5, 10)

Review correctly pointed out that mere traceability of the state "tokens
deducted, freeze not yet created" is not sufficient — it must be clearly
defined **whether the activity is genuinely unlocked at this moment**.
The answer: **no** — `PENDING_FREEZE` explicitly means "not yet
unlocked," not an implementation detail.

```
PENDING_FREEZE  (freeze_confirmation_deadline_at = commit_time + FREEZE_CONFIRMATION_TIMEOUT)
      │
      │  The Penalty Engine creates a freeze_periods row (started_at=now,
      │  expires_at = started_at + policy.maximum_unlock_duration — COMPUTED
      │  EXACTLY ONCE, here; see penalty_window_technical_design.md 3.3)
      │  and confirms back (consuming the activity_authorization.committed event, 8.3)
      ▼
   ACTIVE  (session_expires_at = the SAME value as freeze_periods.expires_at,
            copied from the event payload, NEVER recomputed here)
      │
      │  ◄── ONLY NOW may the system tell the user "you are unlocked"
      ▼
   (see 8.5 — PENDING_RESUME, the symmetric counterpart of this transition)
```

`FREEZE_CONFIRMATION_TIMEOUT` (a configurable parameter) is applied as
an **absolute** `freeze_confirmation_deadline_at`, written already at
the transition into `PENDING_FREEZE` (8.2) — not as an in-memory timer
that a process restart would lose (see Section 16).

If confirmation from the Penalty Engine does not arrive even after
`freeze_confirmation_deadline_at`:

```python
def handle_freeze_confirmation_timeout(db: Database, decision_id: str, now: datetime) -> None:
    """
    Called both reactively (scheduler/reconciliation) and at process
    startup (16) for every PENDING_FREEZE with deadline_at <= now. A
    safe failure: PENDING_FREEZE -> FAILED, and SIMULTANEOUSLY a
    compensating REVERSAL of the previously written SPEND (see 5.3) --
    the user must not be left in a situation of 'paid but did not
    receive.' Atomic (the decision state + the REVERSAL + the event
    within a single transaction, the same _apply_transition pattern
    used elsewhere). Idempotent: if the decision has already transitioned
    to ACTIVE in the meantime (confirmation arrived just before the
    timeout), this function is a no-op.
    """
```

This makes `PENDING_FREEZE` behave as a full-fledged saga with explicit
compensation on failure, rather than merely a diagnostic note for later
manual review.

### 8.5 The Unlock Lifecycle: `ActivityAuthorizationSession` (Fixes for Points 6, 8, 10)

`maximum_unlock_duration` (point 1) is a safety **cap**, not the
expected normal way of ending the unlock. Normal termination is an
explicit user action. Formalized as:

```python
class AuthorizationSessionStatus(StrEnum):
    ACTIVE = "active"
    ENDED_BY_USER = "ended_by_user"     # the normal, expected way of ending
    EXPIRED = "expired"                  # a safety net, not the primary path

@dataclass
class ActivityAuthorizationSession:
    id: str
    authorization_decision_id: str       # FK, created at the moment the decision transitions to ACTIVE (8.4)
    started_at: datetime                  # = freeze_periods.started_at (the SAME value, not a new computation)
    ended_at: datetime | None
    expires_at: datetime                  # = freeze_periods.expires_at (the SAME value — the single source of truth, fix for Point 10)
    status: AuthorizationSessionStatus
```

**`expires_at` is never computed independently inside Activity
Authorization** — it is a literal copy of the value the Penalty Engine
computed and wrote into `freeze_periods.expires_at` at the moment the
freeze was created (8.4), carried across via the payload of the
confirmation event. If both sides computed `now + maximum_unlock_duration`
independently, they could drift apart (even if only by milliseconds, due
to event-delivery latency) — that is exactly what this avoids.

**Closure is symmetric with creation (8.4), not immediate:**

```
ACTIVE
      │
      │  end_session(ENDED_BY_USER) — an explicit user action
      ▼
PENDING_RESUME  (the decision transitions to PENDING_RESUME, session.ended_at=now
                 is written right away — but the lifecycle decision is NOT yet CLOSED)
      │
      │  emits activity_authorization.resume_requested
      │  The Penalty Engine closes the corresponding freeze_periods row
      │  (ended_at=now) and confirms back
      ▼
   CLOSED   ◄── ONLY NOW is the entire cross-module lifecycle genuinely complete
```

```python
def end_session(db: Database, session_id: str, ended_by: AuthorizationSessionStatus, now: datetime) -> None:
    """
    Writes session.ended_at=now (status per ended_by) and transitions
    the decision ACTIVE -> PENDING_RESUME (fix for Point 8 — no longer
    directly to CLOSED). Emits activity_authorization.resume_requested
    with authorization_decision_id in the payload.
    """

def handle_resume_confirmed(db: Database, decision_id: str, event: DomainEvent) -> None:
    """
    Consumes confirmation from the Penalty Engine (freeze_periods.ended_at
    has been written). Only here: the decision transitions
    PENDING_RESUME -> CLOSED. Idempotent with respect to event.id — a
    second delivery of the same confirmation is a no-op.
    """
```

`CLOSED` therefore means precisely what review required: **the entire
cross-module lifecycle is genuinely complete**, not merely that a
request for completion was sent. No tokens are refunded on closure — the
authorized activity has already taken place; `PENDING_RESUME` resolves
only the consistency between the lifecycle state and the actual state of
`freeze_periods`, not money.

**Automatic expiration** (without an explicit user action) goes through
the same pair of steps symmetrically, just from the opposite side — the
Penalty Engine detects it first (`ensure_current_state()`,
`expires_at <= now`, penalty_window_technical_design.md 4.5), closes
`freeze_periods` itself, and emits `penalty_engine.freeze_expired`;
Activity Authorization consumes this event, sets
`session.status=EXPIRED`, `ended_at=now`, and only then transitions the
decision to `CLOSED`. The order "the Penalty Engine closes → emits →
Activity Authorization closes" is **the same regardless of whether the
user or automatic expiration triggered closure** — only who initiates
the step differs (Activity Authorization via `resume_requested`, or the
Penalty Engine itself via `freeze_expired`). This eliminates the risk
review flagged: a session `EXPIRED` while the freeze remains open (or
vice versa), because both sides now share one defined order, rather than
two independent paths.

---

## 9. Events and Audit

| event_type | source_module | When It Occurs |
|---|---|---|
| `activity_authorization.requested` | activity_authorization | a new `ActivityAuthorizationRequest` — persisted immediately (4.0) |
| `activity_authorization.decided` | activity_authorization | `authorize_activity()` returns a preliminary decision, including `DENIED` — persisted immediately (4.2) |
| `activity_authorization.confirmation_received` | activity_authorization | a new `ActivityAuthorizationConfirmationCommand` (CONFIRM or DECLINE) |
| `activity_authorization.declined` | activity_authorization | `lifecycle_status → DECLINED` |
| `activity_authorization.confirmation_expired` | activity_authorization | `lifecycle_status → EXPIRED` (timeout, the absolute `confirmation_expires_at`) |
| `activity_authorization.committed` | activity_authorization | `commit_authorization()` succeeds — carries `freeze_penalty_window` in the payload |
| `activity_authorization.commit_failed` | activity_authorization | `commit_authorization()` fails — a context (PW/policy) or final concurrency check (8.2) — `lifecycle_status → FAILED` |
| `activity_authorization.freeze_confirmed` | activity_authorization | `PENDING_FREEZE → ACTIVE` (8.4) — the payload carries `expires_at` (the single source of truth, fix for Point 10). Published by this module only, triggered by consuming the Penalty Engine's canonical `freeze_periods.opened` event (`penalty_window_technical_design.md` 4.2), filtered to `reason=partnered_intimacy_authorization` — resolved via `docs/architecture/domain_events_catalog.md` Finding 2 (one publisher per event; the earlier ambiguity between this document and `system_state_machine.md` about whether the Penalty Engine or Activity Authorization emits this event is resolved in favor of Activity Authorization, since it is this module's own lifecycle transition) |
| `activity_authorization.freeze_confirmation_failed` | activity_authorization | a timeout on confirming the freeze (`freeze_confirmation_deadline_at`) — `FAILED` plus a compensating `REVERSAL` (8.4) |
| `activity_authorization.resume_requested` | activity_authorization | `end_session()` — `lifecycle_status → PENDING_RESUME` (8.5, fix for Point 8) |
| `activity_authorization.resume_confirmed` | activity_authorization | `PENDING_RESUME → CLOSED`, triggered by consuming the Penalty Engine's canonical `freeze_periods.closed` event, filtered to the same reason — same resolution as `.freeze_confirmed` above |
| `penalty_engine.freeze_expired` | *(consumed, not emitted by this module — see penalty_window_technical_design.md 4.2/4.5)* | automatic expiration initiated by the Penalty Engine, emitted alongside the canonical `freeze_periods.closed` in the same transaction — Activity Authorization reacts by closing the session as `EXPIRED` and the decision `→ CLOSED` (8.5) |
| `urge_disclosure.recorded` | activity_authorization | `URGE_DISCLOSURE` — never leads to `decided`/`committed` |
| `token_ledger.entry_recorded` | activity_authorization | any new `TokenLedgerEntry` |
| `token_ledger.entry_reversed` | activity_authorization | `reverse_entry()` |

`activity_authorization.decided` is emitted even for `DENIED` — a
denial is an audit-worthy event just like an approval (and explicitly
does NOT create Trust Evidence or an Incident, AP17/`philosophy.md`
4.3). Likewise, `declined`/`confirmation_expired` are full-fledged audit
events, not "nothing happened" (fix for Point 2 — the phrasing "as if
the request never occurred" was inaccurate and is replaced: no
functional effect, but audit-recorded).

All events in this table are subject to the transactional outbox pattern
defined in `penalty_window_technical_design.md` (the `domain_events`
table, the `published_at`/`delivery_attempts` columns, invariant I23) —
no parallel mechanism is introduced here.

---

## 10. Invariants

| # | Source | Invariant |
|---|---|---|
| AP1 | philosophy.md 4.2 | A `PROHIBITED` activity has no `ActivityPolicy`, and `authorize_activity()` returns `DENY_PROHIBITED_ACTIVITY` for it before ever looking at tokens or PW state — no code path combines `PROHIBITED` with token/debt logic. |
| AP4/AP17 | philosophy.md 4.3 | `URGE_DISCLOSURE` never creates an `ActivityAuthorizationDecision`, `TrustEvidence`, or an `Incident`. Type-separated from `USER_REQUEST` (4.1). A denied `DENY_*` decision likewise generates no Trust Evidence or Incident. |
| DA1 | 6 | No `TokenLedgerEntry` of type `SPEND` may be created if it would result in `resulting_balance < ActivityPolicy.minimum_balance` — checked preliminarily in `authorize_activity()`. |
| DA1-CONCURRENT | 8.2, fix for Point 4 | DA1 is **bindingly** re-enforced inside `commit_authorization()`, against the balance read under per-user serialization (a lock/serializable transaction/CAS) in the same step as the `SPEND` write. The preliminary evaluation in `authorize_activity()` alone is never sufficient as the final guarantee. |
| POLICY-DURATION-1/2/3 | 3.1, fix for Point 1 | `maximum_unlock_duration` is `None` exactly when `freezes_penalty_window=False`; positive and non-zero when `True`; a `critical_change` parameter. |
| PW-CONTEXT-1 | 3.1, fix for Point 7 | `applies_during_penalty_window` is evaluated based on the existence of a non-completed Penalty Window, never based on the `FROZEN`/`ACTIVE` distinction — no code path in Activity Authorization calls/checks `is_frozen()`. |
| AP6/AP7 | philosophy.md 4.5 | `ActivityPolicy` (prices, `minimum_balance`, `maximum_unlock_duration`) and `ActivityDefinition.category` are `critical_change` — creation/modification always requires `created_via_consent_id`. |
| I21 (shared with Penalty Window) | philosophy.md 4.4 | At most one open `freeze_periods` row with `reason='partnered_intimacy_authorization'` per window — enforced on the Penalty Engine side (a partial unique index); Activity Authorization does not need to know about this, since it never writes there. |
| AP9–AP12 | philosophy.md 4.4 | `ActivityAuthorizationDecision` never contains a field that would directly change `penalty_windows.status`/`extensions_hours`/`base_duration_hours` — only `freeze_penalty_window: bool`, which the Penalty Engine interprets itself. |
| AP18 | philosophy.md 4.4 | `TokenLedgerEntry.user_id` is always the system's user — no structure in this model allows creating a record, condition, or obligation directed at another person (a partner). |
| LEDGER1 | 5.3 | `TokenLedgerEntry` is append-only — the access layer provides no `UPDATE`/`DELETE`. Reversal goes exclusively through `reverse_entry()`. |
| LEDGER2 | 5.1 | `current_balance()` is always defined as the sum of `delta` across all entries for a given `user_id` — no other definition of the balance (e.g., a materialized value with special update logic) exists as the source of truth. |
| LEDGER3 | 4.0, fix for Point 3 | Idempotency of the `SPEND` write arising from an authorization is enforced by `UNIQUE(authorization_decision_id) WHERE entry_type='SPEND'` — not by a separately computed heuristic key. |
| IDEM1 | 4.0, fix for Point 3 | `request_id` and `confirmation_command_id` are generated by the client/Conversation Layer and are `UNIQUE` in the DB — two distinct requests/confirmations arriving close together in time never merge just because they "look similar." |
| AA-COMMIT1 | 8.2 | `commit_authorization()` writes the `ActivityAuthorizationDecision` (lifecycle transition), the `TokenLedgerEntry`, and the `domain_event` atomically (one transaction) within the Activity Authorization module. It never writes to `penalty_windows`/`freeze_periods` directly. Whether atomicity across `commit_authorization()` and the Penalty Engine is achievable (event-driven vs. future transactional orchestration) remains an open question — see 8.3, 14. |
| CTX-REVALIDATE | 8.2, fix for Point 9 | `commit_authorization()` re-verifies the Penalty Window context (the existence of a non-completed window, if `freeze_penalty_window=True`) and uses the `policy` pinned via `decision.policy_id` — it never assumes the snapshot context from the request is still valid. A context change between the request and the commit results in a safe `FAILED`, not silent use of a stale assumption. |
| POLICY-PIN-1 | 4.1, fix for Point 9 | Every `ActivityAuthorizationDecision` carries a `policy_id` referencing a SPECIFIC, immutable version of `ActivityPolicy`. A newer version arising via the consent flow between the request and the commit does not change an already-created decision. |
| EXPIRES-1 | 8.4/8.5, fix for Point 10 | `expires_at` is computed exactly once, by the Penalty Engine, at the moment the `freeze_periods` row is created. Activity Authorization never recomputes it — `ActivityAuthorizationSession.expires_at` is always a literal copy of the value carried via the event payload. |
| LIFECYCLE-1 | 4.1, fix for Point 12 | Every `ActivityAuthorizationDecision` has exactly one `lifecycle_status` from the closed set, never `None`. A `DENY_*` decision is created directly with `lifecycle_status=DENIED`. |
| SAGA1 | 8.4, fix for Point 5 | The activity is never considered unlocked (and the system never tells the user so) before `lifecycle_status == ACTIVE` — that is, until the Penalty Engine confirms the corresponding `freeze_periods` row was created. `PENDING_FREEZE` is not equivalent to being unlocked. |
| SAGA2 | 8.4 | If confirmation of the freeze does not arrive within the limit (`freeze_confirmation_deadline_at <= now`), the previously written `SPEND` must be compensated (`REVERSAL`) atomically with the `FAILED` write — it must never remain "paid with no effect" as a permanent state. |
| SESSION1 | 8.5, fix for Point 6 | Normal termination of an unlock is `end_session()` initiated by the user; automatic expiration (`expires_at`) is a safety-net mechanism, not the primary expected path of termination. |
| SESSION2 (PENDING-RESUME) | 8.5, fix for Point 8 | Closing a session is symmetric with its creation: `ACTIVE → PENDING_RESUME → CLOSED`, never directly `ACTIVE → CLOSED`. `CLOSED` means the Penalty Engine has confirmed closure of the corresponding `freeze_periods` row, not merely that it was requested. This holds identically for user-initiated termination and for automatic expiration — in both cases, the order is "the Penalty Engine closes → emits → Activity Authorization closes." |
| API-BOUNDARY-1 | 16.3, review "recovery must not bypass module boundaries" | Activity Authorization never reads `freeze_periods` directly — neither during normal operation nor during crash recovery. The only permitted path for determining freeze state is `penalty_engine.get_authorization_freeze_state()` (penalty_window_technical_design.md 2.5). This applies to diagnostic/remedial functions too (`find_uncommitted_freeze_requests()` was removed for this reason, 11.3). |
| LEASE-1 | `system_state_machine.md` Section 7, review "startup must be single-writer" | `on_system_startup()` (owned by the runtime/bootstrap layer, not this module — System State Machine Finding 4) acquires a restart-safe database `system_startup_lease` BEFORE any recovery step. At most one process instance may perform startup reconciliation at a time — enforced by an atomic DB write (`acquire_system_startup_lease`), not by an in-memory mutex or a PID-file check. |
| CLAIM-1 (shared with Penalty Window) | penalty_window_technical_design.md 4.6, I23 | The outbox publisher claims `domain_events` rows via `claimed_at`/`claim_expires_at` before delivery — safe even with multiple concurrent publisher processes. Delivery is at-least-once; `published_at` does not mean success at the consumer, only success handing off to the transport layer. |
| CLOCK-1 | 16.7, review "system clock" | All time in the system (Penalty Window, Trust Manager, Activity Authorization) comes from a single injected `Clock`, never from a direct call to `datetime.now()`. `MonotonicGuardedClock` guarantees that the application's `now` never falls below a previously observed value — a backward system clock jump never extends an already-reached deadline. Significant jumps (in either direction) are logged. |

---

## 11. Failure and Retry Semantics

### 11.1 Request-Level Idempotency (Fix for Point 3 — See 4.0)

Idempotency is no longer handled by a separate heuristic function
(`compute_idempotency_key()` from the previous version of this document
was removed entirely, not adjusted) — it is enforced directly by the
identity of the command generated by the client/Conversation Layer
(`request_id`, `confirmation_command_id`), with a `UNIQUE` constraint in
the DB (4.0). A double-click or a network retry reuses the same ID a
second time → the second write attempt fails at the database level,
rather than at the level of guessing "this looks like the same
message."

A `TokenLedgerEntry` arising from an authorization has idempotency via
`UNIQUE(authorization_decision_id) WHERE entry_type='SPEND'` (LEDGER3) —
a repeated call to `commit_authorization()` with the same `decision.id`
never creates a second spend.

### 11.2 Failures at Individual Steps of the Flow

| Failure | Detectability | Recovery |
|---|---|---|
| `commit_authorization()` fails on context revalidation (the PW ended in the meantime, a conflicting session) | `lifecycle_status == FAILED`, `reason_code="CONTEXT_CHANGED_*"`/`"CONFLICTING_OPEN_SESSION"` | No `SPEND` was created (8.2). The user gets a clear message and can request again (a new request accounts for the current context) |
| `commit_authorization()` fails on the final concurrency check (DA1-CONCURRENT) | `lifecycle_status == FAILED`, `reason_code="DA1_RECHECK_FAILED_CONCURRENT_SPEND"` | No `SPEND` was created (8.2). The user gets a clear message and can retry as a new request (a new `request_id`) |
| `commit_authorization()` fails entirely otherwise (the transaction rolls back) | Standard — no partial state arises | The user gets an error and can retry the request |
| `commit_authorization()` succeeds (`SPEND` written), but the event never reaches/is never processed by the Penalty Engine | `activity_authorization.committed` exists, `lifecycle_status` gets stuck in `PENDING_FREEZE` past `freeze_confirmation_deadline_at` | A timeout (8.4, `SAGA2`) → `FAILED` plus a compensating `REVERSAL`, triggered either reactively or during startup reconciliation (16) |
| The Penalty Engine processes the event twice (a redelivered retry) | Consumer idempotency (the same pattern used elsewhere in the system) | The second processing is a no-op — no duplicate `freeze_periods` row (additionally protected by the I21 unique index) |
| The user does not respond to a debt confirmation | `lifecycle_status == PENDING_CONFIRMATION` persists, `confirmation_expires_at` is absolute | Once `confirmation_expires_at <= now` → `EXPIRED` (terminal, no effect), detected reactively or at startup |
| `end_session()`/automatic expiration: the event to the Penalty Engine is not delivered | `lifecycle_status` gets stuck in `PENDING_RESUME` | Startup reconciliation (16), or a publisher retry (the outbox, I23), redelivers `resume_requested`; idempotent on the Penalty Engine side |

### 11.3 Startup Reconciliation Replaces the Consistency Check

The original proposal (`find_uncommitted_freeze_requests()`, a
diagnostic function scanning `activity_authorization.committed` events
against freeze state) has been replaced entirely — for two reasons:

1. It was "a recommendation, not a requirement to implement right
   away," which turned out to be insufficient for a system running on a
   computer that is routinely turned off.
2. Internally, it would have needed to know whether
   `freeze_periods.authorization_decision_id` exists — a direct read of
   another module's table, the same boundary violation the fix below
   addresses.

Both are replaced by **`recover_activity_authorization_state()`**
(16.3) — it walks non-terminal decisions one by one and, for each,
queries exclusively via `penalty_engine.get_authorization_freeze_state()`
(the public API, penalty_window_technical_design.md 2.5), never by
reading the table directly. It is called mandatorily as part of system
startup (`system_state_machine.md` Section 7), not as an optional
periodic audit.

---

## 12. Scenarios

**Scenario A — pornography never goes through, even with extra tokens.**
`ActivityAuthorizationRequest(activity_type='pornography', ...)`.
`authorize_activity()` finds `ActivityDefinition.category == PROHIBITED`
and returns `DENY_PROHIBITED_ACTIVITY` immediately — independent of
`current_balance` (the function never even looks at it, AP1).

**Scenario B — admitting temptation leads to support, not a decision.**
The user mentions strong temptation toward `pornography` in
conversation. `ActivityRequestSource.URGE_DISCLOSURE` — no
`ActivityAuthorizationDecision` is created, no `TokenLedgerEntry`, no
`TrustEvidence`. An `urge_disclosure.recorded` event is created, which
triggers the `UrgeSupportProtocol` (content outside the scope of this
document).

**Scenario C — solo release, insufficient tokens.**
`current_balance=0`, the `solo_release` policy has `minimum_balance=0,
token_cost=1`. `prospective_balance = -1 < 0` → `DENY_INSUFFICIENT_TOKENS`.
No debt is created (unlike partnered intimacy), because
`minimum_balance=0` is a hard cap for this activity (AP2).

**Scenario D — partnered intimacy during a PW, debt with confirmation (updated with lifecycle, policy pinning, revalidation).**
`active_penalty_window_id` is set (a non-completed window exists,
regardless of FROZEN/ACTIVE — 3.1), `current_balance=1`, the "during
PW" policy `policy_v3`: `token_cost=2, minimum_balance=-2,
explicit_confirmation_required_for_debt=True`. `authorize_activity()`
returns a preliminary decision with `policy_id=policy_v3.id` (pinned),
`confirmation_expires_at=now+15min`, `lifecycle_status=PENDING_CONFIRMATION`
— **persisted immediately** (4.0). The user confirms
(`ActivityAuthorizationConfirmationCommand`) → `PENDING_COMMIT`.
`commit_authorization()` reloads `policy_v3` by `policy_id` (even if
`policy_v4` arose in the meantime via the consent flow, `v3` is still
used — auditability), verifies the active PW still exists (the context
has not changed), verifies the balance under the lock (still `1`),
writes `SPEND -2`, and transitions to `PENDING_FREEZE` with
`freeze_confirmation_deadline_at=now+X`. The Penalty Engine processes
the event, creates the `freeze_periods` row with `started_at`/
`expires_at` (computed there, once), and confirms back → `ACTIVE`;
`session_expires_at` in Activity Authorization is a literal copy of
`freeze_periods.expires_at`. Only now does the system tell the user
"you are unlocked" (SAGA1), and an `ActivityAuthorizationSession` is
created.

**Scenario G — concurrent requests, the second one fails safely (fix for Point 4).**
`current_balance=2`, `solo_release` outside a PW: `token_cost=2,
minimum_balance=0`. Two requests (A, B) arrive practically
simultaneously; both pass `authorize_activity()` with the preliminary
`current_balance=2` → both preliminarily `ALLOW_WITH_TOKEN`. During
`commit_authorization()`, however, the per-user lock (8.2) serializes
their writes: A reads the actual balance `2`, writes `SPEND -2`,
succeeds. B (after the lock is released) reads the actual balance `0`;
`actual_prospective_balance = -2 < minimum_balance(0)` →
`commit_authorization()` safely fails into `FAILED`
(`DA1_RECHECK_FAILED_CONCURRENT_SPEND`); no second `SPEND` is created.
User B gets a clear message rather than a silent inconsistency.

**Scenario H — normal session termination, symmetric with opening (fix for Point 8).**
The session is `ACTIVE`, `expires_at` is 3 hours out (a single value, a
copy of `freeze_periods.expires_at`). After an hour, the user writes
"we're done" → `end_session(ended_by=ENDED_BY_USER)`. The session gets
`ended_at=now`, `lifecycle_status: ACTIVE → PENDING_RESUME` (NOT
directly `CLOSED`), and emits `resume_requested`. The Penalty Engine
closes the corresponding `freeze_periods` (`ended_at=now`) and confirms
back. Activity Authorization consumes the confirmation,
`lifecycle_status: PENDING_RESUME → CLOSED`. Only now is the lifecycle
genuinely complete. Alternatively, had the user never ended the
session: the Penalty Engine, during `ensure_current_state()`, detects
`expires_at <= now`, closes `freeze_periods` itself, and emits
`penalty_engine.freeze_expired`; Activity Authorization consumes this
event symmetrically (`session.status=EXPIRED`, `PENDING_RESUME →
CLOSED`) — the same sequence of steps, just a different initiator.

**Scenario E — closing intimacy, but the window remains FROZEN due to an exemption.**
The Penalty Window has an open `freeze_periods` with
`reason='temporary_wear_exemption'` (the user is on vacation) at the
same time as a newly created one with
`reason='partnered_intimacy_authorization'`. Once the intimacy unlock
ends, only the second record is closed — the window remains `FROZEN`,
because the exemption is still running (I22/PW-FREEZE-SET, handled
exclusively on the Penalty Engine side).

**Scenario F — earning after debt automatically reduces the negative value.**
`current_balance=-2` following Scenario D. The user completes an
activity that grants a token → an `EARN` entry, `delta=+1`.
Recomputing `current_balance()` yields `-1`. No special "repayment"
logic was needed — it is just a sum (5.1).

---

## 13. Test Matrix

| # | Scenario | Given | When | Then |
|---|---|---|---|---|
| AT1 | Prohibited cannot be unlocked with a token | `pornography`, any `current_balance` | `authorize_activity()` | `DENY_PROHIBITED_ACTIVITY`, no ledger write (AP1) |
| AT2 | Prohibited cannot be unlocked with debt | same | attempt to create an `ActivityPolicy` for `pornography` | impossible — `PROHIBITED` activities have no `ActivityPolicy` in the model at all |
| AT3 | Urge disclosure generates no decision | — | an `URGE_DISCLOSURE` request | no `ActivityAuthorizationDecision`, no `TrustEvidence`/`Incident` (AP4) |
| AT4 | Denial generates no Trust Evidence | any `DENY_*` | check the Trust Manager after the decision | no new `TrustEvidence` (AP17) |
| AT5 | Solo release never creates debt | `current_balance=0`, `minimum_balance=0` | a request for `solo_release` | `DENY_INSUFFICIENT_TOKENS`, not `ALLOW_WITH_LIMITED_DEBT` (AP2) |
| AT6 | Partnered intimacy respects the debt cap | `current_balance=-1`, policy `minimum_balance=-2` | a request, `token_cost=2` → `prospective=-3` | `DENY_INSUFFICIENT_TOKENS` — `-3 < -2` (DA1) |
| AT7 | The request and decision are persisted immediately, even before confirmation | a new request with `requires_user_confirmation=True` | `authorize_activity()` returns a preliminary decision | both the `ActivityAuthorizationRequest` and the `ActivityAuthorizationDecision` (state `PENDING_CONFIRMATION`) are in the DB immediately; no `TokenLedgerEntry` (4.0, 4.2, fix for Point 2) |
| AT8 | A decline is audit-recorded, with no functional effect | decision `PENDING_CONFIRMATION` | the user sends `DECLINE` | `lifecycle_status → DECLINED`, no ledger write, no freeze request, but the `declined` event and the row itself remain readable in history (4.2, fix of the "as if it never happened" phrasing) |
| AT9 | Idempotency of the request/confirmation via explicit IDs | a request/confirmation sent with `request_id`/`confirmation_command_id` | the same request delivered a second time (retry) | `UNIQUE(request_id)`/`UNIQUE(confirmation_command_id)` rejects the duplicate write (IDEM1, fix for Point 3) |
| AT10 | Earning automatically reduces debt | `balance=-2` | `EARN +1` | `current_balance()==-1`, no special repayment operation (5.1, Scenario F) |
| AT11 | The ledger is append-only | an existing entry | attempt an `UPDATE`/`DELETE` | the method does not exist (LEDGER1) |
| AT12 | A reversal is a compensating record | an erroneous `SPEND` entry | `reverse_entry()` | a new `REVERSAL` entry with the opposite delta; the original is unchanged |
| AT13 | A prohibition during a PW takes precedence over tokens | `solo_release`, PW active, `prohibited_while_window_active=True`, sufficient tokens | a request | `DENY_ACTIVE_RESTRICTION`, not `ALLOW_*` (4.1 — order check) |
| AT14 | Activity Authorization does not write to freeze_periods directly (first implementation) | a successful authorization with `freeze_penalty_window=True` | check who performed the `INSERT` into `freeze_periods` | the write was performed exclusively by the Penalty Engine after consuming the event, not by Activity Authorization (AA-COMMIT1) — holds for the event-driven variant; under a future transactional orchestration, the test would verify the same write ownership, just within a coordinated transaction |
| AT15 | At most one open intimacy freeze | the window already has an open `freeze_periods` with `reason='partnered_intimacy_authorization'` | a second authorization with the same effect | the Penalty Engine rejects/blocks the second INSERT (I21, unique index) |
| AT16 | A concurrent freeze with a different reason does not conflict | the window is `FROZEN` due to `temporary_wear_exemption` | authorizing partnered intimacy | a second, independent `freeze_periods` row is created without issue (I22) |
| AT17 | Resume occurs only after the last reason | the window has 2 open freeze reasons | only one is closed | `status` remains `FROZEN`, no `penalty_window.resumed` event (I22, Scenario E) |
| AT18 | Authorization does not extend/shorten the PW | a successful authorization with a freeze | check `base_duration_hours`/`extensions_hours` | unchanged (AP9–AP12) |
| AT19 | The policy is a critical_change | an attempt to change `token_cost` outside the consent flow | — | rejected/impossible (AP6/AP7) |
| AT20 | No link to another person | any `TokenLedgerEntry`/`ActivityAuthorizationDecision` | check the data model | no field allows storing an obligation/condition directed at a partner (AP18) |
| AT21 | Startup reconciliation finds an uncommitted freeze request via the public API | `commit_authorization()` ran, the event unprocessed past `freeze_confirmation_deadline_at` | `recover_activity_authorization_state()` | calls `get_authorization_freeze_state()`, not a direct read of `freeze_periods`; results in FAILED+REVERSAL or a publish retry, per the outcome (11.3, 16.3, API-BOUNDARY-1) |
| AT22 | `maximum_unlock_duration` must be None without a freeze | a policy with `freezes_penalty_window=False` | an attempt to set `maximum_unlock_duration != None` | rejected (POLICY-DURATION-1) |
| AT23 | `maximum_unlock_duration` must be positive with a freeze | a policy with `freezes_penalty_window=True` | an attempt to set `maximum_unlock_duration=None` or `<=0` | rejected (POLICY-DURATION-2) |
| AT24 | Concurrent spending never exceeds minimum_balance | `balance=2`, two concurrent requests with `cost=2`, `minimum_balance=0` | both `commit_authorization()` calls run nearly simultaneously | one succeeds, the other fails into `FAILED` with no `SPEND` written (DA1-CONCURRENT, Scenario G) |
| AT25 | The activity is not considered unlocked during PENDING_FREEZE | `lifecycle_status=PENDING_FREEZE` | check what the system tells the user | no confirmation of being unlocked until the state transitions to `ACTIVE` (SAGA1) |
| AT26 | A freeze-confirmation timeout leads to compensation | `PENDING_FREEZE` with no confirmation past the limit | `handle_freeze_confirmation_timeout()` | `lifecycle_status → FAILED`; a `REVERSAL` entry canceling the original `SPEND` is created; both atomically (SAGA2) |
| AT27 | Closure is symmetric, not immediate | session `ACTIVE` | `end_session(ENDED_BY_USER)` | `lifecycle_status → PENDING_RESUME` (NOT `CLOSED`); a `resume_requested` event; `CLOSED` only after the Penalty Engine confirms (SESSION2, fix for Point 8) |
| AT28 | Automatic expiration goes through the same pair of steps as end_session | session `ACTIVE`, the user does not terminate it | `expires_at` reached at the Penalty Engine | the Penalty Engine closes the freeze first, emits `freeze_expired`, Activity Authorization reacts with `EXPIRED`+`CLOSED` — the same order as AT27, a different initiator (SESSION1, SESSION2) |
| AT29 | Policy selection does not depend on the FROZEN state | an active PW, `status=FROZEN` (due to `temporary_wear_exemption`) | a request for partnered intimacy | the "during PW" policy is applied (the higher price), not "outside PW" — because the window still exists and is not `COMPLETED` (PW-CONTEXT-1, fix for Point 7) |
| AT30 | Context revalidation — the PW ended between the request and the commit | decision `PENDING_COMMIT`, `freeze_penalty_window=True` | the PW transitions to `COMPLETED` in the meantime | `commit_authorization()` fails into `FAILED` (`CONTEXT_CHANGED_PW_NO_LONGER_ACTIVE`), no `SPEND` (CTX-REVALIDATE) |
| AT31 | Revalidation — a conflicting open session | another `ACTIVE`/`PENDING_FREEZE` decision with the same `reason` on the same window | a new request for partnered intimacy | `commit_authorization()` fails into `FAILED` (`CONFLICTING_OPEN_SESSION`) before wasting a `SPEND` (CTX-REVALIDATE, a soft check ahead of I21) |
| AT32 | Policy pinning survives a newer version | decision `PENDING_CONFIRMATION` with `policy_id=v3` | `v4` arises via the consent flow, then the user confirms | `commit_authorization()` uses `v3`, not `v4` (POLICY-PIN-1) |
| AT33 | `expires_at` is a single value, not two independent computations | a freeze is confirmed | compare `freeze_periods.expires_at` and `ActivityAuthorizationSession.expires_at` | identical; the latter is a literal copy of the former (EXPIRES-1) |
| AT34 | DENIED is a full-fledged lifecycle state | `authorize_activity()` returns `DENY_*` | check `lifecycle_status` | equal to `DENIED`, never `None` (LIFECYCLE-1) |
| AT35 | A PENDING_FREEZE timeout is idempotent against a late confirmation | `PENDING_FREEZE`, `freeze_confirmation_deadline_at` just passed, but confirmation arrived just before it | `handle_freeze_confirmation_timeout()` is called | a no-op, because `lifecycle_status` is already `ACTIVE` (SAGA2, idempotency) |

---

## 14. Open Questions Before Implementation

1. **Atomicity across Activity Authorization and the Penalty Engine
   (Section 8.3)** — remains explicitly open. Two real paths:
   - **Event-driven** (described in 8.3 as the functional solution for
     the first implementation): two independent commits plus an
     idempotent event, a short window of inconsistency between them,
     addressed by startup reconciliation (16).
   - **Transactional orchestration** (Transaction Coordinator / Unit of
     Work): the Penalty Engine remains the sole owner of
     `freeze_periods`, but an orchestrator coordinates a single
     transaction across both modules — full atomicity without breaching
     ownership boundaries. This requires a new architectural layer
     (see the note on the Decision Orchestrator at the end of the
     document), which we do not yet have designed.
   For the first implementation, I propose proceeding with the
   event-driven variant (it is ready to use right away, now also with
   full startup reconciliation, 16), with the understanding that moving
   to an orchestrated transaction remains an open option for a future
   version, not a settled dispute.
2. ~~Enforcement of `maximum_unlock_duration`~~ — **resolved**:
   implemented in `penalty_window_technical_design.md` Section 4.5
   (`ensure_current_state()` extended, `freeze_periods.expires_at` as
   the single source of truth).
3. ~~The exact definition of `compute_idempotency_key()`~~ —
   **moot**: the function was removed entirely, replaced by explicit
   IDs generated by the client (4.0).
4. **The concrete value of `CONFIRMATION_WINDOW`/
   `FREEZE_CONFIRMATION_TIMEOUT`** (4.3, 8.4) — I have proposed an
   illustrative 15 minutes for debt confirmation; the exact values of
   both timeouts are a parameter for your decision, not an
   architectural question.
5. **The restart-safe lock — a concrete mechanism** (8.2, 16) — SQLite
   `BEGIN IMMEDIATE` is probably sufficient for single-process operation
   on one computer (no need for a distributed lock). Do you agree with
   this as the default, or would you like to consider an alternative
   (optimistic CAS on `TokenBalanceCache`) from the start?
6. **The publisher process for the outbox** (I23 in the Penalty Window
   document) — should it run as part of the application's main event
   loop (the Discord bot process), or as a separate thread/process?
   This mainly affects delivery latency, not correctness (correctness
   is guaranteed by the `published_at` mechanism regardless of where the
   publisher runs).

---

## 15. Note on Future Architecture: the Decision Orchestrator

This document covers the third module (after the Trust Manager and the
Penalty Engine) that communicates with the others through manually
maintained, one-directional dependencies (read-only references plus
events). So far this works cleanly, because every boundary was designed
individually and carefully (the Trust Manager writes nowhere; Activity
Authorization reads Penalty Window state and only writes events; the
Penalty Engine is the sole owner of `penalty_windows`/`freeze_periods`).

With three modules this is manageable through explicit review, as done
here. With additional modules (the mentioned `Exception Policy`,
possibly a future `should_extend()` as a fourth consumer), the risk
grows — without a visible structure, a web of direct cross-module
dependencies could gradually emerge that would be hard to keep
consistent with Principle 2.8 (technically enforceable auditability)
and with the one-directional dependencies we have insisted on so far.

A **Decision Orchestrator** would be a thin coordination layer above the
modules — it would determine the *order* of calls (Trust Manager →
Activity Authorization → Penalty Engine → ...), and could potentially
serve as the place for future transactional coordination (see 8.3), but
it would **never take over the decision-making logic** of any of them.
It would be analogous to the `decision_engine.py` of the main Decision
Engine (Coach/Keyholder), just at the level of "who asks whom in what
order," not "who is right."

I am deliberately **not designing this now** — for now, three modules
with three clear, manually verified boundaries are not enough to justify
an orchestration layer beyond a premature abstraction. I mention it as
something to watch for: once a fourth module is designed (or
`should_extend()` becomes an active consumer of more than one of them),
it is worth explicitly asking whether manual boundary review still
suffices, or whether it is time to actually design the orchestrator.

---

## 16. Crash/Restart Recovery

The system runs on an ordinary computer that is routinely turned off and
on — not on a server that assumes continuous uptime. This section
defines how every in-progress state behaves during and after a process
outage/restart, and why what is described above (persisted states,
absolute timestamps, a transactional outbox) is not, by itself,
sufficient without explicit startup reconciliation.

### 16.1 Principle: the Database Is the Source of Truth, Not Process Memory

Five properties this section enforces as a whole (summarized here, with
detail below):

1. **No important timeout exists only as an in-memory timer.** All of
   them (`confirmation_expires_at`, `freeze_confirmation_deadline_at`,
   `session.started_at`, `session.expires_at`, `freeze_periods.started_at`,
   `freeze_periods.expires_at`) are absolute UTC timestamps in the DB —
   this is already part of the data model from Section 4.1/8.5; here it
   is simply enforced as an overall principle.
2. **An uncommitted transaction rolls back entirely on a crash**
   (a standard property of SQLite transactions, no new mechanism) —
   after a restart, only rows that genuinely exist in the DB are
   processed; the system never decides based on what the process
   "probably managed" to send.
3. **After startup, BEFORE accepting any new request**, deterministic
   reconciliation of all in-progress states runs — not reactively,
   "whenever the next message arrives."
4. **Module boundaries hold in recovery code too.** Recovery is easily
   written "quickly," and boundaries between modules are most easily
   crossed there of all places — Activity Authorization therefore
   determines freeze state exclusively via
   `penalty_engine.get_authorization_freeze_state()` (16.3), never by
   reading `freeze_periods` directly, not even in recovery.
5. **Startup reconciliation is a single-writer operation**, protected
   by a restart-safe database lease (`system_state_machine.md` Section
   7), and all time in the system comes from a single injected `Clock`
   (16.7) — both so that an
   accidentally launched second process instance, or a system clock
   drift, cannot cause inconsistency that the other three properties
   would otherwise guarantee against.

### 16.2 Startup Reconciliation — Cross-Reference

`recover_activity_authorization_state()` (16.3) recovers Activity
Authorization's own persisted state. The complete system startup
sequence — the single-writer lease mechanism, the full ordering across
every module, and the reasoning behind that order — is defined
authoritatively in `system_state_machine.md`, Section 7 (per the
System State Machine integration audit, Finding 4: startup
orchestration is a runtime/bootstrap responsibility, not Activity
Authorization's). This document no longer duplicates that definition.

### 16.3 `recover_activity_authorization_state()` — Behavior by State

```python
def recover_activity_authorization_state(db: Database, now: datetime) -> None:
    """
    Walks all NON-TERMINAL ActivityAuthorizationDecision rows and moves
    each one idempotently to wherever it belongs, based on persisted
    data and the current `now`. Safe to run multiple times in a row —
    the second and subsequent calls are no-ops wherever reconciliation
    has already occurred.

    CRITICAL (fix per review, "recovery must not bypass module
    boundaries"): wherever the freeze state tied to this decision needs
    to be known, the call is EXCLUSIVELY to
    penalty_engine.get_authorization_freeze_state()
    (penalty_window_technical_design.md 2.5) — NEVER a direct read of
    freeze_periods. Activity Authorization does not know, and must not
    know, that foreign table's schema, not even in recovery code.
    """
    for decision in db.get_nonterminal_decisions():
        match decision.lifecycle_status:
            case AuthorizationLifecycleStatus.PENDING_CONFIRMATION:
                _recover_pending_confirmation(db, decision, now)
            case AuthorizationLifecycleStatus.PENDING_COMMIT:
                _recover_pending_commit(db, decision, now)
            case AuthorizationLifecycleStatus.PENDING_FREEZE:
                _recover_pending_freeze(db, decision, now)
            case AuthorizationLifecycleStatus.ACTIVE:
                _recover_active(db, decision, now)
            case AuthorizationLifecycleStatus.PENDING_RESUME:
                _recover_pending_resume(db, decision, now)
```

| State | Recovery Behavior |
|---|---|
| `PENDING_CONFIRMATION` | If `confirmation_expires_at > now`: nothing (keep waiting). If `<= now`: transition to `EXPIRED` (terminal, no `SPEND` was ever created). |
| `PENDING_COMMIT` | Must not be left hanging indefinitely. If confirmation exists but the commit did not run, recovery safely and idempotently calls `commit_authorization()` again — `UNIQUE(authorization_decision_id) WHERE entry_type='SPEND'` (LEDGER3) prevents a double deduction if the commit did, after all, run in the meantime. |
| `PENDING_FREEZE` | Calls `state = penalty_engine.get_authorization_freeze_state(decision.id)` (the public API, not a direct read). `state == OPEN`: transition to `ACTIVE`, create an `ActivityAuthorizationSession` (idempotently, if it does not already exist) — obtaining `started_at`/`expires_at` the usual way, via the event payload or a supplementary field in the returned API state. `state == NOT_FOUND` and `freeze_confirmation_deadline_at > now`: republish/redeliver the `activity_authorization.committed` event (an outbox retry). `state == NOT_FOUND` and `<= now`: `handle_freeze_confirmation_timeout()` (8.4) — `FAILED` plus exactly one `REVERSAL`. |
| `ACTIVE` | Calls `get_authorization_freeze_state()` — expects `OPEN`. If `session.expires_at <= now`: **immediately** begin termination (the same path as automatic expiration, 8.5) — downtime is counted toward `expires_at` (16.4). If the session is still valid: no change to the accounting/lifecycle state, only possibly restoring in-memory scheduling (e.g., a scheduler for future expiration). |
| `PENDING_RESUME` | Calls `state = penalty_engine.get_authorization_freeze_state(decision.id)`. `state in (CLOSED, EXPIRED)`: transition to `CLOSED`. `state == OPEN`: republish/redeliver the `resume_requested` event. |

### 16.4 What Counts Toward Downtime — an Explicit Rule

The exact rule (shared with the Penalty Window document, 4.5, repeated
here in relation to Activity Authorization):

```
ACTIVE Authorization Session:
  maximum_unlock_duration runs according to real (wall-clock) time, so
  it CAN expire even while the computer is off. session.expires_at <= now
  is evaluated the same way whether the process ran continuously or was
  off — no "gifted" extra time just because the computer was not running.

PENDING_CONFIRMATION / PENDING_FREEZE deadlines:
  the same rule — an absolute timestamp versus now, regardless of the
  process's running time.
```

An example from review: `session.expires_at = 22:00`, the computer off
from 21:30–23:00. After starting at 23:00,
`recover_activity_authorization_state()` finds an `ACTIVE` session with
`expires_at (22:00) <= now (23:00)` and immediately begins termination
(the same path as the automatic expiration in 8.5) — the system does not
"gift" the user any extra time.

### 16.5 Restart-Safe Locks (Summary; See 8.2 for Detail)

Per-user write serialization (DA1-CONCURRENT) must be **database-backed**
(SQLite `BEGIN IMMEDIATE`, or an equivalent), never just an in-memory
mutex — an in-memory lock disappears after a restart and protects
nothing. This is the same invariant as in 8.2, repeated here as part of
the overall principle in 16.1 (the database is the source of truth).

### 16.6 Test Matrix — Restart Scenarios

| # | Scenario | Crash/Restart Condition | Expected Behavior After Startup |
|---|---|---|---|
| RT1 | Shutdown before confirmation | A crash between `authorize_activity()` and the user's response | `PENDING_CONFIRMATION` persists; recovery leaves it waiting if `confirmation_expires_at > now`, otherwise `EXPIRED` |
| RT2 | Shutdown after confirmation, before SPEND | A crash between writing the `ActivityAuthorizationConfirmationCommand` and `commit_authorization()` | Recovery finds `PENDING_COMMIT`, safely calls `commit_authorization()` again — idempotent via `UNIQUE(authorization_decision_id)` |
| RT3 | Shutdown during a SQL transaction | A crash in the middle of the `commit_authorization()` transaction | The SQLite transaction rolls back entirely — no partial `SPEND`, the decision remains in `PENDING_COMMIT` as if the commit never happened (standard rollback) |
| RT4 | Shutdown after SPEND, before publishing the freeze event | A crash between the commit and writing `published_at` in the outbox | The `SPEND` exists, the event exists with `published_at IS NULL` — `publish_pending_outbox_events()` delivers it after startup (I23) |
| RT5 | Shutdown after freeze creation, before ACTIVE confirmation | A crash between the creation of `freeze_periods` and the return delivery of confirmation | Recovery in `PENDING_FREEZE` finds the existing `freeze_periods` (via `authorization_decision_id`) and transitions directly to `ACTIVE`, creating the session — without waiting for the (lost) event |
| RT6 | Shutdown during an ACTIVE session | A crash, the session still valid | After startup: the session exists, `expires_at > now` → no change, only scheduling is restored |
| RT7 | The session expires during a shutdown | A crash, `expires_at` passes in the meantime | After startup: `expires_at <= now` → termination begins immediately (16.4, the same path as automatic expiration) |
| RT8 | Shutdown after `end_session()`, before resume confirmation | A crash between `PENDING_RESUME` and confirmation from the Penalty Engine | Recovery calls `get_authorization_freeze_state()` — `CLOSED`/`EXPIRED` → `CLOSED`; `OPEN` → redeliver `resume_requested` |
| RT9 | A repeated restart causes no duplicate effect | `on_system_startup()` (`system_state_machine.md` Section 7) run 2x or 10x in a row with no activity in between | The same result as after a single run — no second `SPEND`/`REVERSAL`/`freeze_periods` row (idempotency of all recovery steps) |
| RT10 | A Penalty Window ends during a shutdown | PW `ACTIVE`, `remaining_active_hours` reaches 0 in the meantime | `recover_penalty_window_state()` (Penalty Window document, 4.5/T1) detects completion the same way `ensure_current_state()` would if called while running — downtime counts toward the active countdown |
| RT11 | Two instances run recovery simultaneously | Two processes call `on_system_startup()` at nearly the same time | Only one acquires `system_startup_lease`; the other gets `StartupLeaseNotAcquired` and does not perform reconciliation — no concurrent write from both sides (LEASE-1) |
| RT12 | The publisher crashes after publishing, but before `published_at` | The broker/transport received the message; the process crashed before it could write `published_at` | After a restart: the row has `published_at IS NULL`, the claim has expired (`claim_expires_at`) — a new publisher redelivers it (at-least-once, I23); the consumer deduplicates via `domain_event_consumers` |
| RT13 | The same event is delivered to the consumer repeatedly | The retry from RT12 reaches the same consumer a second time | `domain_event_consumers` `UNIQUE(event_id, consumer_name)` prevents a second effect — a no-op (I19) |
| RT14 | The system clock jumps backward after a restart | After startup, `Clock` returns a system time earlier than previously observed | `MonotonicGuardedClock` (16.7) returns `max(system_now, persisted_last_observed)` — no already-reached deadline gets "pulled back into the future"; the jump is logged (CLOCK-1) |
| RT15 | The system clock jumps forward past several deadlines after a restart | After startup, `Clock` returns a system time hours/days later | All deadlines with `<= now` are evaluated as elapsed (a safe, conservative direction — nothing breaks, the recovery steps simply run all at once); a significant jump is logged as a warning (16.7) |

The same requirement applies to every test: **recovery run once, twice,
or ten times produces the same result** (RT9 tests this explicitly, but
the property must hold across this entire table).

### 16.7 System Time: an Injected `Clock` and Protection Against Drift

Absolute UTC timestamps (16.1) address "where" time is stored, but not
"what happens if the system clock drifts after a restart" — the
hardware clock of an ordinary computer is not a reliable source of
monotonic time across a restart (NTP synchronization, a manual change,
daylight-saving adjustments at the OS configuration level).

```python
class Clock(Protocol):
    def now(self) -> datetime: ...

class SystemClock(Clock):
    """A direct wrapper around datetime.now(timezone.utc) — usable in
    tests for deterministic scenarios (an injected Clock, not a global
    call)."""
    def now(self) -> datetime:
        return datetime.now(timezone.utc)

CLOCK_BACKWARD_JUMP_LOG_THRESHOLD = timedelta(seconds=5)
CLOCK_FORWARD_JUMP_LOG_THRESHOLD = timedelta(minutes=10)

class MonotonicGuardedClock(Clock):
    """
    Used EVERYWHERE in the system (Penalty Window, Trust Manager,
    Activity Authorization) instead of a direct call to datetime.now()
    — a single injected source of time, so that restart and clock-skew
    scenarios can be tested deterministically (by substituting the
    Clock instance in a test).

    Persists the last observed `now` (a single row in the DB). The
    returned value is always max(system_now, persisted_last_observed)
    — the system clock can never move the application's time BACKWARD.
    This guarantees that a once-reached deadline stays reached even
    after the clock drifts backward (no 'extension' of an already
    elapsed deadline).
    """
    def now(self) -> datetime:
        system_now = datetime.now(timezone.utc)
        persisted_last = self._db.get_last_observed_time()

        if persisted_last - system_now > CLOCK_BACKWARD_JUMP_LOG_THRESHOLD:
            log_warning(f"Backward system clock drift detected: {persisted_last} -> {system_now}")
        if system_now - persisted_last > CLOCK_FORWARD_JUMP_LOG_THRESHOLD:
            log_warning(f"Forward system clock drift detected: {persisted_last} -> {system_now}")

        effective_now = max(system_now, persisted_last)
        self._db.set_last_observed_time(effective_now)
        return effective_now
```

This directly addresses the review's requirement: **a backward drift
never spontaneously extends an already-reached deadline** (because,
from the application's perspective, `now` never decreases), and a
**forward drift** is inherently safe (a conservative direction — at
most, recovery steps that would have happened anyway happen all at
once), and is merely logged as a signal worth investigating (it could
indicate faulty clock synchronization, not necessarily a problem with
the system itself).

The `Clock` instance is injected into `on_system_startup()`
(`system_state_machine.md` Section 7),
`ensure_current_state()`, `authorize_activity()`,
`commit_authorization()`, and everywhere else that already used `now:
datetime` as a parameter — those remain unchanged (the functions already
accept `now` as a parameter, rather than determining it themselves);
only where the calling code obtains `now` from changes: always
`clock.now()`, never `datetime.now()` directly.

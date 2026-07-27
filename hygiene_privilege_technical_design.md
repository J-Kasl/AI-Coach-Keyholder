# Hygiene Privilege — Technical Design (v2)

> Draft for review, **not implemented**. Based on `philosophy.md` v1.8
> Section 3.9 (Hygiene Trust Level, Penalty Window Override, Effective
> Hygiene Policy, the priority order, and the score-to-level mapping
> table). Builds on `trust_manager_technical_design.md` (the `hygiene`
> Trust domain, read-only) and `penalty_window_technical_design.md`
> (Penalty Window existence/status, `get_penalty_window_relevant_domains()`
> Section 2.6, and `ensure_current_state()` Section 4.4).
>
> **v2 — fixes from review:** (1) consumption now occurs at the moment a
> Discretionary Hygiene Break session actually **starts**, never at the
> moment permission is granted (Variant B, confirmed) — this required
> introducing a genuine multi-step lifecycle and, with it, real crash
> recovery, replacing v1's "no recovery needed" claim entirely; (2) the
> hygiene-specific Penalty Window override severity (default vs.
> exceptional) is now a persisted, PW-scoped `HygienePenaltyOverrideDetermination`,
> not a per-call parameter that would silently reset on the next
> evaluation; (3) the binding weekly-quota check is now a single
> serialized transaction, protecting against concurrent requests
> jointly over-granting; (4) idempotency via client-generated IDs at
> every step (request, start, end), the same discipline as Activity
> Authorization 4.0; (5) `ensure_current_state()` is now called before
> any Penalty Window context is read; (6) the established numeric policy
> values are used directly — the `TBD_*` placeholders from v1 are
> resolved; (7) "a denied Discretionary request is never Trust Evidence
> or an Incident" is stated as its own explicit rule, not derived "by
> extension" from the Mandatory-access rule.
>
> Status: **draft for approval, not implemented.**

---

## 1. Scope and Module Boundaries

This module answers two related questions: *what Discretionary Hygiene
Break policy currently applies?* and *has a specific break actually been
used?* It does not decide anything about Mandatory Hygiene/Health
Access — that path bypasses this module entirely (see `philosophy.md`
3.9, and `penalty_window_technical_design.md` invariant I14,
`HygieneAccessDecision` for `MANDATORY` requests).

```
Trust Manager      -> hygiene Trust score (context, read-only)
Penalty Engine      -> PW existence/status, and which domain(s) it relates to (context, read-only)
Hygiene Privilege -> what Discretionary policy applies, and whether a break has been used (decision + lifecycle)
```

Dependencies are one-directional, following the same pattern as Trust
Manager, Penalty Engine, and Activity Authorization:

- Hygiene Privilege **reads** the `hygiene` Trust domain's
  `TrustDomainState` (Trust Manager) — read-only, never writes to it,
  never generates `TrustEvidence`.
- Hygiene Privilege **reads** the existence/status of the active or
  frozen Penalty Window directly (the established Activity Authorization
  pattern), and, when one exists, calls
  `get_penalty_window_relevant_domains()`
  (`penalty_window_technical_design.md` 2.6) — never reads
  `freeze_periods` or `incidents` directly.
- Hygiene Privilege **calls `ensure_current_state()`**
  (`penalty_window_technical_design.md` 4.4) as the first step of any
  operation that reads Penalty Window context, exactly like every other
  consumer of Penalty Window state — a stale, not-yet-reconciled
  `ACTIVE` window must never be allowed to drive an override decision.
- Hygiene Privilege **never writes** to `penalty_windows`,
  `freeze_periods`, `incidents`, or any Trust Manager table, and never
  calls `freeze()`/`resume()` or anything that would extend a Penalty
  Window.
- Mandatory Hygiene/Health Access requests never reach this module — see
  `HygieneAccessDecision` in the Penalty Window document.

Outside the scope of this document: the physical mechanics of unlocking
(this module reasons only about *permission* and *usage bookkeeping* —
`philosophy.md` 3.9 already distinguishes access from physical removal),
and the eventual `Device Access`/`Lock Controller` integration mentioned
during architecture review, which remains deferred until a physical
integration (e.g., Chaster) is actually being built.

---

## 2. Data Model

### 2.1 The Three Concepts, as Types (Unchanged From v1)

```python
class HygieneTrustLevel(IntEnum):
    """Levels 1-4 only. Level 0 deliberately does not exist as a member
    of this enum -- see 2.4. Using IntEnum keeps the ordering explicit
    and prevents an accidental 0 from type-checking."""
    LEVEL_1 = 1
    LEVEL_2 = 2
    LEVEL_3 = 3
    LEVEL_4 = 4


class PenaltyWindowOverrideKind(StrEnum):
    HYGIENE_SPECIFIC = "hygiene_specific"
    UNRELATED = "unrelated"


class EffectivePolicySource(StrEnum):
    HYGIENE_SPECIFIC_OVERRIDE = "hygiene_specific_override"
    UNRELATED_OVERRIDE = "unrelated_override"
    TRUST_LEVEL = "trust_level"
```

### 2.2 Discretionary Hygiene Policy — Established Values

```python
@dataclass(frozen=True)
class DiscretionaryHygienePolicy:
    id: str
    policy_kind: str                 # 'trust_level' | 'hygiene_specific_default' |
                                       # 'hygiene_specific_exceptional' | 'unrelated_override'
    trust_level: HygieneTrustLevel | None   # populated only when policy_kind == 'trust_level'

    breaks_per_week: int
    session_minutes: int             # informational guidance on expected session length --
                                       # NOT a technically enforced cutoff; see 4.6

    created_via_consent_id: str
    version: int
    supersedes_id: str | None = None
```

The established values (previously listed as `TBD_*` placeholders in
v1, now resolved) are seeded as the initial active rows:

| `policy_kind` | `trust_level` | `breaks_per_week` | `session_minutes` |
|---|---|---:|---:|
| `trust_level` | `LEVEL_1` | 3 | 20 |
| `trust_level` | `LEVEL_2` | 5 | 30 |
| `trust_level` | `LEVEL_3` | 6 | 30 |
| `trust_level` | `LEVEL_4` | 7 | 30 |
| `hygiene_specific_default` | — | 3 | 20 |
| `hygiene_specific_exceptional` | — | 2 | 20 |
| `unrelated_override` | — | 4 | 30 |

`hygiene_specific_default` reuses the `LEVEL_1` values and
`hygiene_specific_exceptional` reuses the values from the original
five-tier table's Level 0 row — consistent with how those tiers were
first proposed, before the score-based mapping (which excludes Level 0
entirely, `philosophy.md` 3.9) was introduced. These rows are seeded via
their own `created_via_consent_id` at setup time, and from then on are
governed like any other `critical_change` parameter (HYG-GOV-1,
Section 7) — this document fixes their *initial* values, not a
permanently hardcoded constant.

### 2.3 Hygiene Trust Level (Derived, Never Stored Independently — Unchanged From v1)

```python
HYGIENE_TRUST_LEVEL_THRESHOLDS: list[tuple[float, HygieneTrustLevel]] = [
    (0.45, HygieneTrustLevel.LEVEL_1),
    (0.70, HygieneTrustLevel.LEVEL_2),
    (0.85, HygieneTrustLevel.LEVEL_3),
    (1.01, HygieneTrustLevel.LEVEL_4),
]

def compute_hygiene_trust_level(hygiene_trust_score: float) -> HygieneTrustLevel:
    for threshold, level in HYGIENE_TRUST_LEVEL_THRESHOLDS:
        if hygiene_trust_score < threshold:
            return level
    return HygieneTrustLevel.LEVEL_4
```

### 2.4 The Hygiene-Specific Override Severity Is Now Persisted and PW-Scoped (Fix for Point 2)

The v1 design accepted `exceptional_severity_justification` as a
per-call parameter to `evaluate_effective_hygiene_policy()`. Review
correctly identified this as broken: without an explicit input on the
*next* call, the exceptional policy would silently disappear, even
though the intent was "the exceptional severity applies for the
duration of this Penalty Window." This is fixed by making the
determination a first-class, persisted, PW-scoped entity.

```python
class HygienePenaltyOverrideSeverity(StrEnum):
    DEFAULT = "default"
    EXCEPTIONAL = "exceptional"


@dataclass(frozen=True)
class HygienePenaltyOverrideDetermination:
    """
    Append-only. One row per determination event tied to a specific
    hygiene-specific Penalty Window. The CURRENT severity for a given
    penalty_window_id is the severity of the most recent row -- the same
    "denormalize the latest, keep full history" pattern used elsewhere
    in this system (e.g., Incident.confirmation, derived from the latest
    ConfirmationRecord in the Trust Manager). If no determination exists
    yet for a window, its severity is DEFAULT.
    """
    id: str
    penalty_window_id: str
    severity: HygienePenaltyOverrideSeverity
    justification: str                    # REQUIRED, non-empty
    created_at: datetime
    created_via_decision_id: str          # traceable to whatever process authorized it -- see Section 10, open question


def get_current_hygiene_override_severity(db: Database, penalty_window_id: str) -> HygienePenaltyOverrideSeverity:
    latest = db.get_latest_hygiene_override_determination(penalty_window_id)
    return latest.severity if latest else HygienePenaltyOverrideSeverity.DEFAULT


def record_hygiene_override_determination(
    db: Database, penalty_window_id: str, severity: HygienePenaltyOverrideSeverity,
    justification: str, created_via_decision_id: str, event: DomainEvent,
) -> HygienePenaltyOverrideDetermination:
    """
    The ONLY way to change the hygiene-specific override severity for a
    PW -- including moving it back to DEFAULT after an EXCEPTIONAL
    determination, which is also modeled as a new row (append-only, not
    a revocation of the previous one). Atomic with its event write (see
    Section 6).
    """
```

`justification` is required and non-empty on every row, including a
`DEFAULT` determination — this keeps the same discipline as
`TrustRecalculation.explanation` (Trust Manager TI10): a decision without
a recorded reason is not a valid decision in this system.

### 2.5 Penalty Window Override Context (Unchanged Shape, Now Requires `ensure_current_state()` First — Fix for Point 5)

```python
@dataclass(frozen=True)
class PenaltyWindowOverrideContext:
    active_penalty_window_id: str | None
    kind: PenaltyWindowOverrideKind | None


def determine_penalty_window_override(db: Database, user_id: str, now: datetime) -> PenaltyWindowOverrideContext:
    """
    Calls ensure_current_state(db, now) FIRST -- penalty_window_technical_design.md
    4.4 requires this before any operation that depends on Penalty
    Window state, and Hygiene Privilege is such an operation. Without
    this, a window that is actually COMPLETED but not yet reconciled
    could still drive an override decision.
    """
    ensure_current_state(db, now)   # penalty_window_technical_design.md 4.4 -- fix for Point 5

    pw = db.get_active_or_frozen_penalty_window(user_id)
    if pw is None:
        return PenaltyWindowOverrideContext(active_penalty_window_id=None, kind=None)

    relevant_domains = get_penalty_window_relevant_domains(db, pw.id)   # penalty_window_technical_design.md 2.6
    kind = (PenaltyWindowOverrideKind.HYGIENE_SPECIFIC
            if "hygiene" in relevant_domains
            else PenaltyWindowOverrideKind.UNRELATED)
    return PenaltyWindowOverrideContext(active_penalty_window_id=pw.id, kind=kind)
```

### 2.6 Effective Hygiene Policy and Its Audit Trail (Unchanged From v1)

```python
@dataclass(frozen=True)
class EffectiveHygienePolicyResult:
    id: str
    evaluated_at: datetime
    user_id: str

    source: EffectivePolicySource
    policy_id: str

    hygiene_trust_score_snapshot: float
    hygiene_trust_level_snapshot: HygieneTrustLevel
    active_penalty_window_id: str | None
    override_severity_snapshot: HygienePenaltyOverrideSeverity | None   # audit only, populated when source is HYGIENE_SPECIFIC_OVERRIDE
```

As in v1: the snapshot fields exist purely for audit. Every evaluation
starts fresh from current state — `TrustDomainState`, Penalty Window
context, and now also the current
`HygienePenaltyOverrideDetermination` — never from a prior
`EffectiveHygienePolicyResult`.

---

## 3. The Effective Policy Algorithm

Unchanged in structure from v1, updated to read the persisted override
severity instead of accepting it as a parameter:

```python
def evaluate_effective_hygiene_policy(db: Database, user_id: str, now: datetime) -> EffectiveHygienePolicyResult:
    """
    Deterministic given current state. Priority order (philosophy.md
    3.9), evaluated in sequence:
      1. Mandatory Hygiene/Health Access -- handled entirely outside this function.
      2. An active hygiene-specific Penalty Window overrides everything below.
      3. An active unrelated Penalty Window overrides the Trust Level.
      4. Otherwise, the Hygiene Trust Level applies.
    """
    pw_context = determine_penalty_window_override(db, user_id, now)
    override_severity_snapshot = None

    if pw_context.kind == PenaltyWindowOverrideKind.HYGIENE_SPECIFIC:
        severity = get_current_hygiene_override_severity(db, pw_context.active_penalty_window_id)   # fix for Point 2
        policy_kind = ("hygiene_specific_exceptional"
                        if severity == HygienePenaltyOverrideSeverity.EXCEPTIONAL
                        else "hygiene_specific_default")
        policy = _load_active_policy(db, policy_kind=policy_kind)
        source = EffectivePolicySource.HYGIENE_SPECIFIC_OVERRIDE
        override_severity_snapshot = severity
        # philosophy.md 3.9, point 2: the Hygiene Trust Level is not
        # consulted at all for this branch's decision -- see HYG-7.

    elif pw_context.kind == PenaltyWindowOverrideKind.UNRELATED:
        policy = _load_active_policy(db, policy_kind="unrelated_override")
        source = EffectivePolicySource.UNRELATED_OVERRIDE

    else:
        trust_state = get_trust_domain_state(db, user_id, domain_id="hygiene")
        level = compute_hygiene_trust_level(trust_state.score)
        policy = _load_active_policy(db, policy_kind="trust_level", trust_level=level)
        source = EffectivePolicySource.TRUST_LEVEL

    current_score = _current_hygiene_score_for_audit(db, user_id)
    result = EffectiveHygienePolicyResult(
        id=new_id(), evaluated_at=now, user_id=user_id, source=source, policy_id=policy.id,
        hygiene_trust_score_snapshot=current_score,
        hygiene_trust_level_snapshot=compute_hygiene_trust_level(current_score),
        active_penalty_window_id=pw_context.active_penalty_window_id,
        override_severity_snapshot=override_severity_snapshot,
    )
    _record_evaluation(db, result)
    return result
```

---

## 4. Requesting, Starting, and Ending a Discretionary Hygiene Break (Rewritten for Variant B)

### 4.1 Why a Multi-Step Lifecycle Is Now Required

Confirmed: **permission does not consume a break; consumption occurs
only when physical unlock actually begins.** This is the same principle
already governing every other unlock in this system (an
`ActivityAuthorizationDecision` does not consume anything until its
`PENDING_FREEZE`/`ACTIVE` transition actually confirms a freeze; a
`PENDING_CONFIRMATION` that expires unused has no effect). Applying it
here means a granted break that is never started must leave the weekly
quota untouched — which requires a lifecycle with at least the states
specified: `GRANTED → STARTED → ENDED`, with `EXPIRED_UNUSED` as the
branch for a grant that lapses before it is ever started.

```python
class DiscretionaryBreakGrantStatus(StrEnum):
    GRANTED = "granted"                  # non-terminal, awaiting the start command
    STARTED = "started"                   # non-terminal, awaiting the end command
    ENDED = "ended"                        # TERMINAL -- normal completion, consumed one use
    EXPIRED_UNUSED = "expired_unused"      # TERMINAL -- never started, consumed nothing
    DENIED = "denied"                      # TERMINAL -- immediate, from the preliminary check
    FAILED_AT_START = "failed_at_start"    # TERMINAL -- the binding recheck at start failed (concurrency or a context change)
```

### 4.2 Requesting and Granting (Preliminary, Non-Consuming)

```python
GRANT_VALIDITY_WINDOW = timedelta(minutes=30)   # parameter, configurable


@dataclass(frozen=True)
class DiscretionaryHygieneBreakRequest:
    request_id: str          # client-generated, UNIQUE, stable across retries -- same pattern as Activity Authorization 4.0
    created_at: datetime
    user_id: str


@dataclass
class DiscretionaryHygieneBreakGrant:
    """
    MUTABLE row with its own status -- the same pattern as
    ActivityAuthorizationDecision.lifecycle_status. Every transition is
    atomic with its domain_event (Section 6).
    """
    id: str
    request_id: str                 # FK, UNIQUE -- one grant per request (idempotency, fix for Point 4)
    created_at: datetime
    user_id: str
    status: DiscretionaryBreakGrantStatus

    effective_policy_result_id: str       # snapshot of the policy that authorized the grant -- PRELIMINARY, re-checked at start
    grant_expires_at: datetime | None     # absolute deadline to start; None once STARTED or terminal
    started_at: datetime | None
    ended_at: datetime | None
    reason_code: str
    explanation: str


def request_discretionary_hygiene_break(db: Database, request: DiscretionaryHygieneBreakRequest, now: datetime) -> DiscretionaryHygieneBreakGrant:
    """
    PRELIMINARY step. Persists the request immediately (audit trail
    exists even for a DENIED outcome, mirroring Activity Authorization
    4.0/4.2). Evaluates the effective policy and a preliminary quota
    check against currently STARTED sessions this week -- informational
    only, since granting does not itself consume anything (Variant B).
    The BINDING check happens later, at start_hygiene_break_session()
    (4.3).
    """
    with db._connect() as conn:
        _insert_request(conn, request)

        policy_result = evaluate_effective_hygiene_policy(conn, request.user_id, now)
        policy = _load_policy_by_id(conn, policy_result.policy_id)
        preliminary_usage = _count_started_sessions_in_rolling_week(conn, request.user_id, now)

        if preliminary_usage >= policy.breaks_per_week:
            grant = DiscretionaryHygieneBreakGrant(
                id=new_id(), request_id=request.request_id, created_at=now, user_id=request.user_id,
                status=DiscretionaryBreakGrantStatus.DENIED,
                effective_policy_result_id=policy_result.id, grant_expires_at=None,
                started_at=None, ended_at=None,
                reason_code="WEEKLY_LIMIT_REACHED",
                explanation=f"Weekly limit reached ({policy.breaks_per_week}/week under {policy_result.source.value}).",
            )
        else:
            grant = DiscretionaryHygieneBreakGrant(
                id=new_id(), request_id=request.request_id, created_at=now, user_id=request.user_id,
                status=DiscretionaryBreakGrantStatus.GRANTED,
                effective_policy_result_id=policy_result.id,
                grant_expires_at=now + GRANT_VALIDITY_WINDOW,
                started_at=None, ended_at=None,
                reason_code="GRANTED",
                explanation=f"Granted under {policy_result.source.value}; must be started within {GRANT_VALIDITY_WINDOW}.",
            )

        _insert_grant(conn, grant)
        _write_event(conn, _grant_decided_event(grant))
    return grant
```

A `DENIED` outcome here (`WEEKLY_LIMIT_REACHED`) is a preliminary,
informational denial — the binding decision always happens at start
time, but there is no reason to let an obviously-over-limit request
proceed to `GRANTED` only to fail later; this mirrors
`authorize_activity()` denying early in Activity Authorization 4.3.

### 4.3 Starting a Session — the Binding, Concurrency-Safe Step (Fix for Point 3)

```python
@dataclass(frozen=True)
class HygieneBreakStartCommand:
    start_command_id: str    # client-generated, UNIQUE, stable across retries
    grant_id: str
    created_at: datetime


@dataclass(frozen=True)
class DiscretionaryHygieneBreakSession:
    id: str
    grant_id: str            # FK, UNIQUE -- exactly one session per grant
    started_at: datetime
    ended_at: datetime | None


def start_hygiene_break_session(db: Database, command: HygieneBreakStartCommand, now: datetime) -> DiscretionaryHygieneBreakGrant:
    """
    THE BINDING step (HYG-9, revised). Runs inside a restart-safe,
    per-user serialized transaction -- the same discipline as
    commit_authorization() in Activity Authorization 8.2 (illustrative
    name db._connect_immediate(), see that document's note on
    implementation choice; it MUST be restart-safe, never in-memory-only).
    Two concurrent start attempts must never jointly consume more of the
    weekly quota than the effective policy permits.
    """
    with db._connect_immediate() as conn:
        grant = _load_grant_by_id_locked(conn, command.grant_id)

        if grant.status != DiscretionaryBreakGrantStatus.GRANTED:
            # Already started/ended/expired/failed -- idempotent no-op via
            # start_command_id UNIQUE handles retries; a status check here
            # additionally guards against starting a non-GRANTED grant at all.
            return grant

        if grant.grant_expires_at <= now:
            _transition_grant(conn, grant.id, DiscretionaryBreakGrantStatus.EXPIRED_UNUSED)
            _write_event(conn, _grant_expired_event(grant))
            return _reload_grant(conn, grant.id)

        # Re-evaluate the effective policy FRESH -- context (Trust score,
        # Penalty Window state, override severity) may have changed since
        # the grant was issued. Mirrors CTX-REVALIDATE in Activity
        # Authorization 8.2.
        current_policy_result = evaluate_effective_hygiene_policy(conn, grant.user_id, now)
        current_policy = _load_policy_by_id(conn, current_policy_result.policy_id)

        actual_usage = _count_started_sessions_in_rolling_week_locked(conn, grant.user_id, now)
        if actual_usage >= current_policy.breaks_per_week:
            _transition_grant(conn, grant.id, DiscretionaryBreakGrantStatus.FAILED_AT_START,
                               reason_code="DA_HYG_RECHECK_FAILED_CONCURRENT_OR_CONTEXT_CHANGED")
            _write_event(conn, _grant_failed_event(grant))
            return _reload_grant(conn, grant.id)

        session = DiscretionaryHygieneBreakSession(id=new_id(), grant_id=grant.id, started_at=now, ended_at=None)
        _insert_session(conn, session)
        _transition_grant(conn, grant.id, DiscretionaryBreakGrantStatus.STARTED, started_at=now, grant_expires_at=None)
        _write_event(conn, _session_started_event(session))
        return _reload_grant(conn, grant.id)
```

`start_command_id` is `UNIQUE` in the database, exactly like
`confirmation_command_id` in Activity Authorization (4.0) — a retried
start command (network retry, double-tap) never creates a second
session. `DiscretionaryHygieneBreakSession.grant_id` is also `UNIQUE`,
providing a second, structural guarantee that a grant can be started at
most once.

### 4.4 Ending a Session

```python
@dataclass(frozen=True)
class HygieneBreakEndCommand:
    end_command_id: str      # client-generated, UNIQUE, stable across retries
    session_id: str
    created_at: datetime


def end_hygiene_break_session(db: Database, command: HygieneBreakEndCommand, now: datetime) -> None:
    """
    Normal termination: session.ended_at=now; the corresponding grant
    STARTED -> ENDED. This is the only status transition a completed use
    of a Discretionary Hygiene Break goes through -- there is no
    "PENDING_RESUME"-style intermediate step here, because unlike
    Activity Authorization's freeze, nothing in another module needs to
    confirm this closure (see Section 8).
    """
```

### 4.5 What Counts as "One Use" (Fix for Point 3, Explicit Statement)

**A grant consumes one use of the weekly quota exactly when its session
transitions to `STARTED`** — not at `GRANTED`, and not at `ENDED`.
`_count_started_sessions_in_rolling_week()` counts
`DiscretionaryHygieneBreakSession` rows by `started_at`, regardless of
whether they have since ended. A `GRANTED` grant that expires unused
(`EXPIRED_UNUSED`) or fails at start (`FAILED_AT_START`) consumes
nothing, by construction — there is no session row for either outcome.

### 4.6 Session Duration Is Informational, Not a Technical Cutoff

`session_minutes` on a `DiscretionaryHygienePolicy` communicates the
expected/allowed length of a break, but — unlike Activity
Authorization's `maximum_unlock_duration`, which the Penalty Engine can
technically enforce because it owns the freeze mechanism — this module
has no equivalent physical enforcement point with the current
manual-key-access implementation. A session that runs long is not
automatically ended by this module. If a future `Chaster`-based
integration is built (AI → Chaster API → device → lockbox → PIN),
technical enforcement of `session_minutes` becomes possible and would
belong to a future `Device Access`/`Lock Controller` component, not
here — see Section 1.

---

## 5. Domain Events

| event_type | source_module | When It Occurs |
|---|---|---|
| `hygiene_privilege.policy_evaluated` | hygiene_privilege | any call to `evaluate_effective_hygiene_policy()` — logged even when unchanged from the previous evaluation (HYG-8) |
| `hygiene_privilege.override_determination_recorded` | hygiene_privilege | a new `HygienePenaltyOverrideDetermination` (default or exceptional) |
| `hygiene_privilege.break_requested` | hygiene_privilege | a new `DiscretionaryHygieneBreakRequest` |
| `hygiene_privilege.grant_decided` | hygiene_privilege | a new `DiscretionaryHygieneBreakGrant`, `GRANTED` or `DENIED` |
| `hygiene_privilege.grant_expired_unused` | hygiene_privilege | `GRANTED → EXPIRED_UNUSED` |
| `hygiene_privilege.session_started` | hygiene_privilege | `GRANTED → STARTED`, a new `DiscretionaryHygieneBreakSession` — this is the event that represents actual quota consumption |
| `hygiene_privilege.grant_failed_at_start` | hygiene_privilege | `GRANTED → FAILED_AT_START` |
| `hygiene_privilege.session_ended` | hygiene_privilege | `STARTED → ENDED` |

All events use the transactional outbox already defined in
`penalty_window_technical_design.md` — no new mechanism here.

---

## 6. Persistence and Transaction Boundaries

```python
def _record_evaluation(db, result) -> None: ...          # append-only insert + event, one transaction (unchanged from v1)
def _record_override_determination(db, determination, event) -> None: ...   # append-only insert + event, one transaction

# request_discretionary_hygiene_break(): request + grant + event, one transaction (4.2)
# start_hygiene_break_session(): grant transition + session insert + event, one SERIALIZED transaction (4.3)
# end_hygiene_break_session(): session + grant transition + event, one transaction
```

`DiscretionaryHygieneBreakGrant` is **mutable** (its `status` changes
over its lifecycle) — the same pattern as `penalty_windows.status` and
`ActivityAuthorizationDecision.lifecycle_status`, not an append-only
entity. `DiscretionaryHygieneBreakRequest`,
`DiscretionaryHygieneBreakSession`,
`HygienePenaltyOverrideDetermination`, and `EffectiveHygienePolicyResult`
remain append-only.

---

## 7. Invariants

| # | Source | Invariant |
|---|---|---|
| HYG-1 | 2.2 | For each `policy_kind` (and, where applicable, `trust_level`), exactly one `DiscretionaryHygienePolicy` row is active at any time. |
| HYG-GOV-1 | 2.2, philosophy.md 4.5-style | `DiscretionaryHygienePolicy` rows and `HYGIENE_TRUST_LEVEL_THRESHOLDS` are `critical_change` parameters — creation or modification always requires `created_via_consent_id`. |
| HYG-2 | 2.3 | `HygieneTrustLevel` is never persisted as an independent, authoritative value — always recomputed from the current `TrustDomainState('hygiene').score`. |
| HYG-3 | 3 | `evaluate_effective_hygiene_policy()` never writes to `penalty_windows`, `freeze_periods`, `incidents`, or any Trust Manager table. |
| HYG-4 | 2.4, fix for Point 2 | The hygiene-specific override severity is determined exclusively by the most recent `HygienePenaltyOverrideDetermination` for the current Penalty Window — never by a per-call parameter, never inferred automatically from history or frequency internal to this module. |
| HYG-5 | 4.2, philosophy.md 3.9, fix for Point 7 | A request for a Discretionary Hygiene Break, and its denial (at request time or at start time), are never themselves Trust Evidence or an Incident, and never themselves extend a Penalty Window. This is a standalone rule of this module, not derived by extension from the Mandatory-access frequency rule, which rests on a distinct, health-specific protection. |
| HYG-6 (API-BOUNDARY-3) | 1 | Hygiene Privilege never reads `freeze_periods` or `incidents` directly — only direct reads of `penalty_windows` (existence/status) and `get_penalty_window_relevant_domains()` are permitted. |
| HYG-7 | 3, philosophy.md 3.9 point 2 | While the hygiene-specific override applies, `evaluate_effective_hygiene_policy()` does not read `TrustDomainState('hygiene')` at all as an input to policy selection — only for the audit snapshot, computed after the policy decision is made. |
| HYG-8 | 5 | Every call to `evaluate_effective_hygiene_policy()` writes a `hygiene_privilege.policy_evaluated` event, even when unchanged from the immediately preceding evaluation. |
| HYG-9 (revised scope, fix for Point 3) | 4.3 | Counting current `STARTED` sessions in the rolling week, re-evaluating the effective policy, and transitioning a grant `GRANTED → STARTED` occur in one serialized transaction (`start_hygiene_break_session()`). Two concurrent start attempts may never jointly consume more of the weekly quota than the effective policy permits. `request_discretionary_hygiene_break()` itself requires no such protection, since granting alone never consumes quota (Variant B). |
| HYG-10 | 4.2/4.3, fix for Point 4 | `request_id`, `start_command_id`, and `end_command_id` are client-generated and `UNIQUE` in the database. `DiscretionaryHygieneBreakGrant.request_id` and `DiscretionaryHygieneBreakSession.grant_id` are each `UNIQUE` — a grant is created at most once per request, and started at most once. |
| HYG-11 | 4.5 | Quota consumption is defined exclusively as a `DiscretionaryHygieneBreakSession` row existing (i.e., `GRANTED → STARTED` occurred) — never as a `GRANTED` or `DENIED` grant status alone. `EXPIRED_UNUSED` and `FAILED_AT_START` grants never contribute to `_count_started_sessions_in_rolling_week()`. |
| HYG-12 | 2.5, fix for Point 5 | `determine_penalty_window_override()` calls `ensure_current_state()` before reading any Penalty Window state — a stale, not-yet-reconciled window status never drives an override decision. |

---

## 8. Crash/Restart Recovery (New in v2 — Replaces v1's "No Recovery Needed" Claim)

Variant B introduces genuine non-terminal, multi-step state
(`GRANTED` awaiting a start command; `STARTED` awaiting an end command),
so v1's claim that this module needs no dedicated recovery logic no
longer holds. This section follows the same structure as
`activity_authorization_technical_design.md` Section 16.

### 8.1 Startup Reconciliation

```python
def recover_hygiene_privilege_state(db: Database, now: datetime) -> None:
    """
    Called from on_process_startup() (activity_authorization_technical_design.md
    16.2), inside the same system_startup_lease, after
    recover_penalty_window_state() and recover_activity_authorization_state()
    (order matches their dependency: Hygiene Privilege reads Penalty
    Window state, so Penalty Window recovery must run first). Safe to
    run multiple times in a row.
    """
    for grant in db.get_nonterminal_hygiene_grants():
        match grant.status:
            case DiscretionaryBreakGrantStatus.GRANTED:
                if grant.grant_expires_at <= now:
                    _transition_grant(db, grant.id, DiscretionaryBreakGrantStatus.EXPIRED_UNUSED)
                    _write_event(db, _grant_expired_event(grant))
                # else: leave it waiting -- no action needed, the client
                # may still send a valid start command before expiry.
            case DiscretionaryBreakGrantStatus.STARTED:
                # No automatic action: this module has no technical means
                # to end a session on the user's behalf (4.6). Recovery
                # here is a consistency check only -- confirm exactly one
                # DiscretionaryHygieneBreakSession exists for this grant
                # with ended_at IS NULL, and leave it as-is otherwise.
                _verify_session_consistency(db, grant.id)
```

### 8.2 What Counts Toward `grant_expires_at`

Consistent with `penalty_window_technical_design.md` 4.5 and
`activity_authorization_technical_design.md` 16.4: `grant_expires_at` is
an absolute timestamp, evaluated the same way regardless of whether the
process ran continuously or was restarted in between. Downtime counts
toward it — a grant issued at 14:00 with a 30-minute validity window is
`EXPIRED_UNUSED` at 14:31 whether or not the process was running at
14:15.

### 8.3 Why `STARTED` Needs No Automatic Timeout

Unlike Activity Authorization's `PENDING_FREEZE`/`ACTIVE` (where the
Penalty Engine technically owns and can enforce closing the freeze),
nothing in this system can technically detect or force the end of a
physical hygiene break with the current manual-key-access
implementation (4.6). A `STARTED` grant therefore has no
`expires_at`-style automatic closure — it waits indefinitely for an
`end_hygiene_break_session()` call. This is a deliberate difference from
Activity Authorization, not an oversight: enforcing an artificial
software timeout on a real-world physical action the system cannot
observe or control would be misleading, not safer.

### 8.4 Test Matrix Addition — Restart Scenarios

| # | Scenario | Crash/Restart Condition | Expected Behavior After Startup |
|---|---|---|---|
| HRT1 | Shutdown before a grant's validity window elapses | `GRANTED`, `grant_expires_at` still in the future | Recovery leaves it `GRANTED`, waiting |
| HRT2 | Shutdown after a grant's validity window elapses | `GRANTED`, `grant_expires_at` has passed | Recovery transitions it to `EXPIRED_UNUSED`; no session was ever created, no quota consumed |
| HRT3 | Shutdown during a `STARTED` session | `STARTED`, session `ended_at IS NULL` | Recovery leaves it `STARTED` — no automatic closure (8.3); a consistency check confirms exactly one open session exists |
| HRT4 | Shutdown mid-transaction in `start_hygiene_break_session()` | A crash between the grant transition and the session insert | The transaction rolls back entirely — the grant remains `GRANTED`, no orphaned session (standard SQLite rollback, `philosophy.md` 2.8) |
| HRT5 | Repeated restart causes no duplicate effect | `recover_hygiene_privilege_state()` run 2x or 10x in a row | The same result as a single run — idempotent (status-guarded transitions, the same discipline as Activity Authorization RT9) |

---

## 9. Test Matrix

| # | Scenario | Given | When | Then |
|---|---|---|---|---|
| HT1 | No override, Trust Level applies | no active/frozen PW, `hygiene` Trust score = 0.6 | `evaluate_effective_hygiene_policy()` | `source=TRUST_LEVEL`, `LEVEL_2` (5×/week, 30min) applied |
| HT2 | Hygiene-specific override ignores Trust Level | active hygiene-specific PW, Trust score = 0.95 (would be Level 4), no `HygienePenaltyOverrideDetermination` recorded | `evaluate_effective_hygiene_policy()` | `source=HYGIENE_SPECIFIC_OVERRIDE`, the default policy (3×/week, 20min) applied — NOT Level 4 (HYG-7) |
| HT3 | Exceptional severity persists across evaluations | a hygiene-specific PW has a recorded `EXCEPTIONAL` determination | two separate calls to `evaluate_effective_hygiene_policy()`, no new determination in between | BOTH calls apply the exceptional policy (2×/week, 20min) — the severity does not reset between calls (HYG-4, the core fix for Point 2) |
| HT4 | Recording a new determination changes future evaluations only | a hygiene-specific PW currently at `DEFAULT` | `record_hygiene_override_determination(EXCEPTIONAL, ...)`, then evaluate again | the new evaluation applies the exceptional policy; a `hygiene_privilege.override_determination_recorded` event is emitted |
| HT5 | Unrelated override ignores Trust Level as a level | active PW, relevant domains = `{'routine'}` | `evaluate_effective_hygiene_policy()` | `source=UNRELATED_OVERRIDE`, the fixed unrelated policy (4×/week, 30min) applied |
| HT6 | Override ends, policy recomputed from current Trust score | PW ends between two evaluations; Trust score changed | evaluate after the PW ends | `source=TRUST_LEVEL`, computed fresh — never the level that applied before the PW (philosophy.md 3.9) |
| HT7 | Level 0 never reachable via score alone | `hygiene` Trust score = 0.0, no PW | `compute_hygiene_trust_level(0.0)` | returns `LEVEL_1` — no code path to a Level-0-equivalent policy outside a recorded `EXCEPTIONAL` determination |
| HT8 | A GRANTED grant with no session consumes nothing | a fresh `GRANTED` grant, no `DiscretionaryHygieneBreakSession` row for it | query `_count_started_sessions_in_rolling_week()` | the grant does not appear in the count (HYG-11) |
| HT9 | A grant that is never started consumes nothing | a fresh `GRANTED` grant | `grant_expires_at` passes, `recover_hygiene_privilege_state()` runs | `EXPIRED_UNUSED`; `_count_started_sessions_in_rolling_week()` is unaffected (HYG-11) |
| HT10 | Starting is the binding consumption event | a valid `GRANTED` grant, quota available | `start_hygiene_break_session()` | `STARTED`, a `DiscretionaryHygieneBreakSession` is created, and it now counts toward the weekly quota (4.5, HYG-11) |
| HT11 | Concurrent starts never jointly exceed the quota | two `GRANTED` grants for the same user, one remaining slot in the weekly quota | both call `start_hygiene_break_session()` nearly simultaneously | one succeeds (`STARTED`); the other fails safely into `FAILED_AT_START` — no second session row (HYG-9) |
| HT12 | Idempotent start via retry | a session already `STARTED` from an earlier call with `start_command_id=X` | the same `start_command_id=X` delivered again | `UNIQUE(start_command_id)` prevents a second session; the call is a no-op returning the existing grant (HYG-10) |
| HT13 | Denied request creates no Trust Evidence or Incident | weekly limit reached | `request_discretionary_hygiene_break()` | `DENIED`; no new `TrustEvidence`, no new `Incident`, no Penalty Window Extension (HYG-5) |
| HT14 | Mandatory requests never reach this module | a `MANDATORY` hygiene/health request | any code path | this module's functions are never called (unchanged from v1) |
| HT15 | No direct reads of internal Penalty Engine tables | any evaluation or grant decision | inspect table/function access | only `penalty_windows` (direct) and `get_penalty_window_relevant_domains()` were accessed (HYG-6) |
| HT16 | `ensure_current_state()` runs before override determination | a stale `ACTIVE` window that should be `COMPLETED` | `determine_penalty_window_override()` | `ensure_current_state()` reconciles it first; the resulting override context reflects the RECONCILED state, not the stale one (HYG-12) |
| HT17 | Policy rows are `critical_change` | an attempt to change `breaks_per_week` outside the consent flow | — | rejected without a new versioned row carrying `created_via_consent_id` (HYG-GOV-1) |

---

## 10. Open Questions Before Implementation

1. **Rolling-week definition** for `_count_started_sessions_in_rolling_week()` — a fixed calendar week or a trailing 7-day window (unchanged from v1, still open).
2. **`GRANT_VALIDITY_WINDOW`** (currently proposed as 30 minutes) — a parameter for your decision, not an architectural question.
3. **Who is permitted to call `record_hygiene_override_determination()`**, and what `created_via_decision_id` actually references — presumably the Keyholder engine, following the `analysis → proposal → explanation → approval` shape (`philosophy.md` 2.5), but the exact calling path is not yet specified and may be worth a short, separate note once the Keyholder engine itself is designed (same open question as v1, now scoped more precisely to this one function instead of a loose per-call parameter).
4. **Whether a `STARTED` session should eventually support a manual Keyholder-initiated end** (e.g., if the user cannot or does not end it themselves) — not designed here; `end_hygiene_break_session()` currently assumes the user (or their client) initiates it.

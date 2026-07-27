# Extension — Technical Design (v1.2)

> Draft for review, **not implemented**. Resolves the `should_extend()`
> open question deferred in `penalty_window_technical_design.md`
> Section 6 (item 3). Based on `philosophy.md` v1.12 Section 3.8 (the
> general principles governing Extension: proportionality,
> explainability, auditability, the absolute maximum, and targeting by
> Trust domain) and Section 2.11 (Domain Interpretation). Consumes
> `ExtensionContext` from `trust_manager_technical_design.md` Section 6
> as its only input carrying anything from the Trust Manager — nothing
> else from that module is read.
>
> This document lives inside the Penalty Engine's own domain --
> `should_extend()` and its constituent functions are Penalty Engine
> functions, called from the same incident-consumption flow that
> already exists there (`penalty_window_technical_design.md` 2.2, the
> `(none)/ACTIVE -> ACTIVE` extend transition). It is not a separate
> module.
>
> **v1.1:** confirmed that `occurred_during_recovery_task` affects
> magnitude only, never eligibility (EXT-10, ET16) — closing what was
> open question 5 in Section 10.
>
> **v1.2:** fixed per `system_state_machine.md` Finding 1 —
> `consume_incident_for_active_window()` no longer takes a full
> `Incident` object (that type is owned entirely by the Trust Manager).
> It takes only `incident_id` and obtains the `IncidentAssessment` via
> `get_incident_assessment()` (`trust_manager_technical_design.md`
> Section 13); consumption is recorded in the Penalty Engine's own
> `incident_consumption` table, never on `Incident` itself.
>
> Status: **Architecture baseline — approved for implementation.**
> Reached this status once the eligibility/magnitude/mitigation/capacity
> separation, the recovery-task-context resolution (v1.1), and the
> `Incident` ownership fix (v1.2) were applied — this document is now
> the baseline for Extension's implementation, not a proposal still
> awaiting changes.

---

## 1. Why This Needs Its Own Document

`should_extend()` is not a simple predicate. To satisfy `philosophy.md`
3.8 simultaneously, it must be:

- **proportionate** -- the amount added must track the Incident's actual
  severity, not a flat number;
- **explainable** -- the user can always learn why, and by how much, a
  window was extended;
- **auditable** -- every Extension traces to a specific Incident;
- **bounded** -- it must never contribute to exceeding the absolute
  336-hour maximum (`penalty_window_technical_design.md` I5);
- **targeted** -- it reasons only about the Trust domain of the
  triggering Incident, never about unrelated domains or Trust history
  in general (Section 2.11, applied here).

Satisfying all five at once turns out to require separating two
questions that a single boolean conflates:

```
confirmed Incident (in an active/frozen Penalty Window)
        |
        v
   ELIGIBILITY   -- does this Incident warrant an Extension at all?
        |
        v (if eligible)
   BASE MAGNITUDE -- how much, before any softening?
        |
        v
   MITIGATION     -- cooperation / recovery-task context softens it,
        |            but never erases it for a substantively eligible
        |            MAJOR/CRITICAL Incident
        v
   CAPACITY CAP   -- the absolute 336-hour ceiling, a SEPARATE
        |            constraint from eligibility
        v
   atomic application + audit event
```

---

## 2. Data Model

### 2.1 Eligibility

```python
class ExtensionEligibilityReason(StrEnum):
    ELIGIBLE_BY_SEVERITY = "eligible_by_severity"
    """MAJOR/CRITICAL. Always eligible according to an explicit decision
    table entry, subject only to the structural preconditions already
    enforced before should_extend() is ever called (the Incident is
    CONFIRMED and not yet consumed by another window -- Trust Manager
    TI15; Penalty Window I11/I12). No cooperation-based or
    context-based exception exists for these tiers (EXT-4)."""

    ELIGIBLE_BY_REPETITION = "eligible_by_repetition"
    """MINOR/MODERATE, repeated (same rule_group_id, CONFIRMED) within
    the CURRENT window."""

    ELIGIBLE_BY_LOW_COOPERATION = "eligible_by_low_cooperation"
    """MINOR/MODERATE, first occurrence in the current window, but
    cooperation does not meet the high-cooperation threshold (3.2)."""

    INELIGIBLE_ISOLATED_LOW_SEVERITY = "ineligible_isolated_low_severity"
    """MINOR/MODERATE, first occurrence in the current window, with
    high cooperation. The Incident is still consumed by the window
    (philosophy.md 3.8 -- every Incident occurring during an active
    window is assigned to it) but does not extend it."""
```

### 2.2 The Decision Record

```python
@dataclass(frozen=True)
class ExtensionDecision:
    """
    Append-only. should_extend() never returns a bare bool -- the
    downstream reasoning (why eligible/ineligible, how much before and
    after mitigation, whether the absolute cap limited the result) must
    all be reconstructable from this record without re-deriving it from
    context that may have since changed.
    """
    id: str
    created_at: datetime
    incident_id: str
    penalty_window_id: str

    eligible: bool
    eligibility_reason: ExtensionEligibilityReason

    base_hours: float | None            # None iff not eligible
    mitigation_hours: float             # base_hours - uncapped_hours; 0.0 if not eligible
    uncapped_hours: float | None        # after mitigation, before the capacity cap; None iff not eligible
    assigned_hours: float               # actually applied to extensions_hours; 0.0 if not eligible OR fully capacity-limited
    capacity_limited: bool              # True iff assigned_hours < uncapped_hours -- SEPARATE from eligibility, see EXT-6

    explanation: str                    # REQUIRED, non-empty, regardless of eligible (EXT-7)
```

`eligible=True, assigned_hours=0.0, capacity_limited=True` is a valid,
expected outcome -- a substantively eligible Incident that could not be
assigned any further active time because the absolute maximum was
already reached. This is a categorically different outcome from
`eligible=False`, and the two are never conflated (EXT-6): the former
means "this Incident deserved an Extension, but no capacity remained";
the latter means "this Incident, on its own terms, does not warrant an
Extension."

---

## 3. The Three-Stage Algorithm

### 3.1 Stage One: Eligibility (Determined Once, Never Revisited)

```python
# Parameter, critical_change (mirrors HYG-GOV-1/TI20) -- TBD, see Section 10.
HIGH_COOPERATION_THRESHOLD: float = ...  # placeholder


def _is_high_cooperation(cooperation: CooperationAssessment) -> bool:
    """TBD formula over CooperationAssessment.self_disclosed /
    active_cooperation_in_resolution -- a critical_change parameter, not
    a runtime judgment call."""


def determine_extension_eligibility(context: ExtensionContext) -> tuple[bool, ExtensionEligibilityReason]:
    """
    A deterministic decision table, not a scored/weighted judgment --
    the same discipline as assess_severity() in the Trust Manager.
    Called exactly once per Incident; its result is never revisited by
    any later stage (EXT-3).
    """
    if context.intrinsic_severity in (SeverityTier.MAJOR, SeverityTier.CRITICAL):
        return True, ExtensionEligibilityReason.ELIGIBLE_BY_SEVERITY

    if context.same_rule_confirmed_incident_count_in_current_window > 1:
        return True, ExtensionEligibilityReason.ELIGIBLE_BY_REPETITION

    if not _is_high_cooperation(context.cooperation):
        return True, ExtensionEligibilityReason.ELIGIBLE_BY_LOW_COOPERATION

    return False, ExtensionEligibilityReason.INELIGIBLE_ISOLATED_LOW_SEVERITY
```

`same_rule_confirmed_incident_count_in_current_window` counts only
`CONFIRMED` Incidents with matching `rule_group_id` already consumed by
the **current, still-open** window -- structurally distinct from the
Trust Manager's `RepetitionEvidence.same_rule_confirmed_count`, which
counts across all history, including Incidents consumed by earlier,
already-closed windows (EXT-2). These are two legitimate
interpretations of a similar underlying fact for two different
purposes -- not a duplicated source of truth. Neither function reads
the other's result.

### 3.2 Stage Two: Base Magnitude

```python
# Parameters, critical_change -- TBD, see Section 10.
BASE_HOURS_BY_SEVERITY: dict[SeverityTier, float] = {
    SeverityTier.MINOR: ...,
    SeverityTier.MODERATE: ...,
    SeverityTier.MAJOR: ...,
    SeverityTier.CRITICAL: ...,
}
REPETITION_INCREMENT_HOURS: float = ...


def calculate_base_magnitude(severity: SeverityTier, repetition_count_in_window: int) -> float:
    """
    Deterministic, table-driven. repetition_count_in_window is the SAME
    current-window-scoped count used in eligibility (3.1) -- reused
    here for magnitude, not recomputed differently.
    """
    base = BASE_HOURS_BY_SEVERITY[severity]
    if repetition_count_in_window > 1:
        base += REPETITION_INCREMENT_HOURS * (repetition_count_in_window - 1)
    return base
```

### 3.3 Stage Three: Mitigation (Bounded for MAJOR/CRITICAL)

```python
# Parameters, critical_change -- TBD, see Section 10.
MINIMUM_RETAINED_FRACTION: dict[SeverityTier, float] = {
    SeverityTier.MAJOR: ...,      # e.g., 0.5 -- mitigation may reduce base_hours by at most half
    SeverityTier.CRITICAL: ...,   # e.g., 0.7 -- a higher floor than MAJOR
    # MINOR and MODERATE deliberately have NO entry here -- mitigation
    # may reduce their magnitude toward (though, structurally, never
    # below) zero. The floor exists specifically to prevent a serious
    # Incident's consequence from being mathematically erased by
    # cooperation/context (EXT-5); it is not a general guarantee for
    # every tier.
}


def _mitigation_fraction(cooperation: CooperationAssessment, occurred_during_recovery_task: bool) -> float:
    """TBD formula, returns a value in [0.0, 1.0] -- the proportion of
    base_hours that mitigating context removes, before any floor is
    applied. A critical_change parameter."""


def apply_mitigation(base_hours: float, severity: SeverityTier, cooperation: CooperationAssessment, occurred_during_recovery_task: bool) -> float:
    """
    Softens magnitude; never erases the Extension consequence of a
    substantively eligible MAJOR/CRITICAL Incident while this stage
    still has capacity to give (EXT-5) -- capacity itself is a
    SEPARATE, later concern (3.4), not something this function is aware
    of.
    """
    reduction = _mitigation_fraction(cooperation, occurred_during_recovery_task)
    mitigated = base_hours * (1.0 - reduction)

    floor_fraction = MINIMUM_RETAINED_FRACTION.get(severity)
    if floor_fraction is not None:
        mitigated = max(mitigated, base_hours * floor_fraction)

    return mitigated
```

### 3.4 Stage Four: The Capacity Cap (Structurally Separate From Eligibility)

```python
def apply_capacity_cap(uncapped_hours: float, remaining_active_hour_capacity: float) -> tuple[float, bool]:
    """
    remaining_active_hour_capacity comes from ExtensionContext -- already
    computed elsewhere (the Penalty Engine's own I5 logic,
    penalty_window_technical_design.md), never re-derived here. This
    function does not know about the 336-hour constant at all; it only
    respects whatever capacity it is handed (Domain Interpretation,
    2.11 -- this function receives an already-interpreted number, not a
    raw window state to reach into).
    """
    assigned = min(uncapped_hours, remaining_active_hour_capacity)
    capacity_limited = assigned < uncapped_hours
    return assigned, capacity_limited
```

### 3.5 Putting It Together

```python
def should_extend(context: ExtensionContext, incident_id: str, penalty_window_id: str) -> ExtensionDecision:
    """
    The single entry point. Each stage's output is consumed by the
    next; no stage reaches backward to change an earlier one (EXT-3).
    """
    eligible, reason = determine_extension_eligibility(context)

    if not eligible:
        return ExtensionDecision(
            id=new_id(), created_at=utc_now(), incident_id=incident_id, penalty_window_id=penalty_window_id,
            eligible=False, eligibility_reason=reason,
            base_hours=None, mitigation_hours=0.0, uncapped_hours=None,
            assigned_hours=0.0, capacity_limited=False,
            explanation=f"Not eligible for Extension: {reason.value}. The Incident remains consumed by this window (philosophy.md 3.8) but does not extend it.",
        )

    base_hours = calculate_base_magnitude(context.intrinsic_severity, context.same_rule_confirmed_incident_count_in_current_window)
    uncapped_hours = apply_mitigation(base_hours, context.intrinsic_severity, context.cooperation, context.occurred_during_recovery_task)
    assigned_hours, capacity_limited = apply_capacity_cap(uncapped_hours, context.remaining_active_hour_capacity)

    return ExtensionDecision(
        id=new_id(), created_at=utc_now(), incident_id=incident_id, penalty_window_id=penalty_window_id,
        eligible=True, eligibility_reason=reason,
        base_hours=base_hours,
        mitigation_hours=base_hours - uncapped_hours,
        uncapped_hours=uncapped_hours,
        assigned_hours=assigned_hours,
        capacity_limited=capacity_limited,
        explanation=(
            f"Eligible ({reason.value}). Base {base_hours}h, mitigated to {uncapped_hours}h"
            + (f", capped to {assigned_hours}h by the absolute maximum." if capacity_limited else f", assigned {assigned_hours}h.")
        ),
    )
```

---

## 4. Integration With Incident Consumption

This extends the existing Penalty Engine incident-consumption flow
(`penalty_window_technical_design.md` I11/I12) -- not a new entry
point. Updated per the System State Machine integration audit (Finding
1): the Penalty Engine no longer holds its own copy of `Incident` or
its assessment — it takes only `incident_id` (a
`ConfirmedIncidentSummary` from `get_confirmed_incidents_since()`) and
reads the assessment it needs through the Trust Manager's own
`get_incident_assessment()` (`trust_manager_technical_design.md`
Section 13), rather than an implied direct field access.

```python
def consume_incident_for_active_window(db: Database, incident_id: str, window: PenaltyWindow, now: datetime) -> None:
    """
    Runs atomically: records consumption (write-once, I11, via
    incident_consumption), calls should_extend(), applies
    extensions_hours if assigned_hours > 0, and writes the
    ExtensionDecision plus domain events -- all in the SAME transaction
    (EXT-9, mirrors the _apply_transition pattern used everywhere else
    in this system).
    """
    with db._connect() as conn:
        assessment = get_incident_assessment(conn, incident_id)   # trust_manager_technical_design.md Section 13 -- the ONLY way this data is read (EXT-8, Finding 1)
        trust_domain = _get_confirmed_incident_trust_domain(conn, incident_id)   # from the same ConfirmedIncidentSummary already used to select this Incident

        _insert_incident_consumption(conn, incident_id, window.id, trust_domain, now)   # I11 -- penalty_window_technical_design.md's incident_consumption table

        context = _build_extension_context(conn, assessment, window)   # reads ONLY the IncidentAssessment just obtained + this window's own state (EXT-8)
        decision = should_extend(context, incident_id, window.id)
        _insert_extension_decision(conn, decision)
        _write_event(conn, _extension_decision_event(decision))

        if decision.assigned_hours > 0:
            _apply_extension(conn, window.id, decision.assigned_hours)   # window.extensions_hours += assigned_hours
            _write_event(conn, _penalty_window_extended_event(window, decision))   # penalty_window.extended, penalty_window.target_duration_changed (penalty_window_technical_design.md 4.2)
```

Incident consumption (`philosophy.md` 3.8: every Incident during an
active/frozen window is assigned to it) happens **unconditionally** --
it does not depend on `decision.eligible`. Only the Extension itself
(`extensions_hours`) is conditional.

---

## 5. Domain Events

| event_type | source_module | When It Occurs |
|---|---|---|
| `extension.decision_recorded` | penalty_engine | every call to `should_extend()`, eligible or not |
| `penalty_window.extended` | penalty_engine | only when `assigned_hours > 0` -- already defined in `penalty_window_technical_design.md` 4.2, reused here, not redefined |
| `penalty_window.target_duration_changed` | penalty_engine | only when `assigned_hours > 0` -- already defined there |

All events use the transactional outbox already defined in
`penalty_window_technical_design.md` -- no new mechanism here.

---

## 6. Invariants

| # | Source | Invariant |
|---|---|---|
| EXT-1 | 1, Trust Manager TI6 | `should_extend()` and every function it calls accept only `ExtensionContext` (and the Incident/window identifiers) -- never `TrustDomainState`, a raw Trust score, `confidence`, `trend`, or `OverallTrustReport`. |
| EXT-2 | 3.1, 3.2 | `same_rule_confirmed_incident_count_in_current_window` counts only `CONFIRMED` Incidents with matching `rule_group_id` consumed by the current, still-open window. It is structurally distinct from, and never reads or is read by, the Trust Manager's `RepetitionEvidence.same_rule_confirmed_count`. |
| EXT-3 | 3.5 | Eligibility is determined once, before magnitude or mitigation, and is never revisited by any later stage. No code path allows `apply_mitigation()` or `apply_capacity_cap()` to change `eligible` or `eligibility_reason`. |
| EXT-4 | 2.1, 3.1 | MAJOR/CRITICAL Incidents are eligible according to an unconditional decision-table entry, subject only to the structural preconditions enforced before `should_extend()` is called (a `CONFIRMED`, not-yet-consumed Incident). No cooperation-based or context-based exception exists for these tiers. |
| EXT-5 | 3.3 | For MAJOR/CRITICAL Incidents, `apply_mitigation()` never returns a value below `base_hours * MINIMUM_RETAINED_FRACTION[severity]`. Mitigating factors soften magnitude; they never erase the Extension consequence of a substantively eligible Incident of these tiers. |
| EXT-6 | 2.2, 3.4 | `eligible` (substantive eligibility) and `capacity_limited` (whether the absolute maximum constrained the result) are tracked as separate fields and never conflated. `eligible=True, assigned_hours=0.0, capacity_limited=True` must never be reported or logged as "ineligible." |
| EXT-7 | 2.2 | `ExtensionDecision.explanation` is required and non-empty for every decision, regardless of `eligible`. |
| EXT-8 | 2.11 (Domain Interpretation) | `should_extend()` and its constituent functions never read `TrustDomainState`, `GoalEvidence`, `GoalAccountabilityAssessment`, or any Mandatory Hygiene/Health Access record. Their only inputs are the triggering Incident's `IncidentAssessment` (obtained exclusively via the Trust Manager's `get_incident_assessment()`, never a direct field access) and the current window's own state. |
| EXT-9 | 4 | `extensions_hours` is updated in the same transaction as writing the `ExtensionDecision` and its domain events -- never as a separate step that could leave one without the other. |
| EXT-10 | 3.1, 3.3, resolved open question | `occurred_during_recovery_task` is never read by `determine_extension_eligibility()` -- it is used only by `apply_mitigation()`. Participation in a Recovery Task may soften an Extension's magnitude, but can never make an otherwise eligible Incident ineligible. This holds for every severity tier, including MAJOR/CRITICAL. |

---

## 7. Persistence and Crash Recovery

`ExtensionDecision` is append-only, written exactly once per Incident
consumption, inside the same transaction as everything else in Section
4 (`_apply_transition`-style atomicity, `philosophy.md` 2.8). This
introduces **no new non-terminal, multi-step state** -- an Incident is
either consumed-with-a-decision-recorded, or (on a crash before commit)
not yet processed at all, in which case
`penalty_window_technical_design.md`'s existing incident-registration
path handles it exactly as it would any other pending Incident. No
dedicated recovery section beyond what that document already specifies
is needed here, for the same structural reason Goal Management needed
only minimal recovery (`goal_technical_design.md` 9.2): there is no
waiting state for `should_extend()` itself to get stuck in.

---

## 8. Test Matrix

| # | Scenario | Given | When | Then |
|---|---|---|---|---|
| ET1 | MAJOR is always eligible | `intrinsic_severity=MAJOR`, isolated, high cooperation | `determine_extension_eligibility()` | `eligible=True, reason=ELIGIBLE_BY_SEVERITY` (EXT-4) |
| ET2 | CRITICAL is always eligible | `intrinsic_severity=CRITICAL`, isolated, high cooperation | `determine_extension_eligibility()` | `eligible=True, reason=ELIGIBLE_BY_SEVERITY` (EXT-4) |
| ET3 | Isolated MINOR with high cooperation is ineligible | `MINOR`, `same_rule_confirmed_incident_count_in_current_window=1`, high cooperation | `determine_extension_eligibility()` | `eligible=False, reason=INELIGIBLE_ISOLATED_LOW_SEVERITY` |
| ET4 | Isolated MINOR with low cooperation is eligible | `MINOR`, count=1, low cooperation | `determine_extension_eligibility()` | `eligible=True, reason=ELIGIBLE_BY_LOW_COOPERATION` |
| ET5 | Repeated MINOR is eligible even with high cooperation | `MINOR`, count=2, high cooperation | `determine_extension_eligibility()` | `eligible=True, reason=ELIGIBLE_BY_REPETITION` |
| ET6 | Eligibility is never revisited | any eligible case | `apply_mitigation()`/`apply_capacity_cap()` run afterward, however extreme their inputs | `ExtensionDecision.eligible` remains `True`; nothing downstream can flip it (EXT-3) |
| ET7 | Mitigation floor holds for MAJOR | `MAJOR`, `base_hours=X`, maximal cooperation + `occurred_during_recovery_task=True` | `apply_mitigation()` | result `>= X * MINIMUM_RETAINED_FRACTION[MAJOR]` (EXT-5) |
| ET8 | Mitigation floor holds for CRITICAL | `CRITICAL`, same as ET7 | `apply_mitigation()` | result `>= X * MINIMUM_RETAINED_FRACTION[CRITICAL]` (EXT-5) |
| ET9 | MINOR/MODERATE have no floor | `MINOR`, maximal mitigation | `apply_mitigation()` | result may approach (but structurally never fall below) `0` -- no floor entry applies |
| ET10 | Capacity cap is distinct from eligibility | `CRITICAL`, eligible, `remaining_active_hour_capacity=0` | `should_extend()` | `eligible=True`, `assigned_hours=0.0`, `capacity_limited=True` -- NOT reported as ineligible (EXT-6) |
| ET11 | Repetition count is scoped to the current window only | a `CONFIRMED` Incident with matching `rule_group_id` from a previous, already-closed window | count `same_rule_confirmed_incident_count_in_current_window` for a new window | the old window's Incident does not contribute to the count (EXT-2) |
| ET12 | `explanation` is always populated | any `ExtensionDecision`, eligible or not | inspect `explanation` | non-empty in every case (EXT-7) |
| ET13 | No Trust/Goal/Hygiene state is read | any call to `should_extend()` | inspect all reads performed | only `ExtensionContext` fields and the current window's own state are touched -- no `TrustDomainState`, `GoalEvidence`, `GoalAccountabilityAssessment`, or Mandatory Hygiene record (EXT-1, EXT-8) |
| ET14 | Consumption is unconditional; Extension is not | an `INELIGIBLE` Incident during an active window | `consume_incident_for_active_window()` | a row is inserted into `incident_consumption` regardless; `extensions_hours` is unchanged |
| ET15 | Atomic write | the Section 4 transaction | a simulated crash between the `incident_consumption` insert and writing the `ExtensionDecision` | both roll back together -- no consumption row without a corresponding decision record (EXT-9, the standard `_apply_transition` guarantee) |
| ET16 | Recovery-task context cannot change eligibility | the same Incident context twice, differing only in `occurred_during_recovery_task` | `determine_extension_eligibility()` is called on both | both calls return the same `eligible` value and `eligibility_reason`; only `apply_mitigation()` may produce a different magnitude between the two (EXT-10) |

---

## 9. Relationship to the Absolute Maximum (Defense in Depth)

`penalty_window_technical_design.md` I5 already guarantees
`target_active_hours = min(base_duration_hours + extensions_hours, 336)`
as a read-time invariant, independent of how `extensions_hours` grows.
This document's `apply_capacity_cap()` (3.4) is a **second, earlier**
enforcement of the same ceiling -- it prevents `extensions_hours` from
being incremented past what I5 would allow in the first place, rather
than relying solely on I5 to silently absorb an over-large increment at
read time. Both together mean the 336-hour maximum is never at risk
from a single point of failure in this document.

---

## 10. Open Questions Before Implementation

All of the following are deliberately left as `TBD` parameters, not
architectural questions -- consistent with how
`hygiene_privilege_technical_design.md` Section 3 and
`activity_authorization_technical_design.md` handled their own numeric
placeholders:

1. **`BASE_HOURS_BY_SEVERITY`** -- the starting Extension magnitude for
   each `SeverityTier`.
2. **`REPETITION_INCREMENT_HOURS`** -- how much each additional
   same-rule repetition within the current window adds to the base.
3. **`MINIMUM_RETAINED_FRACTION`** for `MAJOR`/`CRITICAL` -- the exact
   floor ratios (illustrative values of 0.5/0.7 were used above purely
   as placeholders in the code comments, not as a proposal).
4. **`HIGH_COOPERATION_THRESHOLD`** and the exact formula behind
   `_is_high_cooperation()`/`_mitigation_fraction()` -- how
   `CooperationAssessment`'s fields translate into a 0.0-1.0 reduction
   and into the binary eligibility-side threshold used for
   MINOR/MODERATE.

**Resolved design decision (no longer open):** `occurred_during_recovery_task`
affects magnitude only, never eligibility — see EXT-10. Confirmed
explicitly: a Recovery Task in progress is a context the Incident
occurred in, not a property of the Incident itself, and never grants
immunity from an otherwise-warranted Extension, including for
MAJOR/CRITICAL severity.

# Trust Manager — Technical Design (v2)

> Draft for review, **not implemented**. Based on `philosophy.md` v1.3,
> and replaces v1 of this document — incorporates 8 points from review
> (genuinely append-only evidence, separation of `should_extend()`
> inputs, confirmation gating, separating intrinsic severity from
> cooperation, structured repetition evidence, restricted manual review,
> explicit score-transition invariants, refinement of SUSTAINED_PERIOD).
>
> `should_extend()` is **not** designed in this document — it is
> explicitly the next step, after this document is approved.

---

## 1. Conceptual Model

Unchanged from v1: Trust in a domain is an estimate of how much autonomy
is currently safe and reasonable to grant the user in that specific area,
based on current and long-term evidence of their behavior in that
area — a prospective estimate, not a retrospective judgment, not a moral
or punitive score.

The Trust Manager is a provider of context, not a decision-making
authority. It never calls `penalty_engine` itself and never writes to
`penalty_windows`.

---

## 2. Data Structures

### 2.1 Domain Registry

Unchanged from v1:

```python
@dataclass(frozen=True)
class TrustDomain:
    domain_id: str
    display_name: str
    description: str
    is_active: bool = True
    created_at: datetime
    created_via_consent_id: str
    deactivated_at: datetime | None = None
    deactivated_via_consent_id: str | None = None
    # Optional initial values specified at the moment the domain is
    # created (see 3.4) — if not provided, system defaults are used.
    initial_score_override: float | None = None
    initial_confidence_override: float | None = None
```

### 2.2 Domain State

```python
@dataclass(frozen=True)
class TrustDomainState:
    domain_id: str
    score: float                       # 0.0–1.0
    confidence: float                  # 0.0–1.0
    trend: str                         # 'improving' | 'declining' | 'stable'
    last_recalculated_at: datetime
    last_relevant_event_at: datetime | None
```

### 2.3 Evidence — Now Genuinely Append-Only (Fix for Point 1)

```python
@dataclass(frozen=True)
class TrustEvidence:
    id: str
    domain_id: str
    created_at: datetime
    evidence_type: EvidenceType         # see 2.4

    source_entity_type: str             # 'incident' | 'recovery_credit_ledger' | 'manual_review' | ...
    source_entity_id: str

    raw_weight: float                   # signed, BEFORE confidence scaling
    evidence_confidence: float          # 0.0–1.0
    explanation: str                    # REQUIRED
```

`TrustEvidence` no longer **contains** `applied` or
`applied_in_recalculation_id`. Once written, a row never changes again —
not through any field, in any situation. This is a literal fulfillment of
the append-only principle, not merely "usually doesn't change."

The fact that a piece of evidence was used in a recalculation is a
**separate, new fact**, not a modification of an existing one:

```python
@dataclass(frozen=True)
class TrustRecalculationEvidence:
    """A join table, likewise append-only."""
    recalculation_id: str
    evidence_id: str                    # UNIQUE — a piece of evidence may be
                                         # consumed exactly once (see TI4)
    effective_weight: float             # raw_weight * evidence_confidence,
                                         # computed and written at the moment of consumption
    created_at: datetime
```

`UNIQUE(evidence_id)` on this table does exactly what the old `applied`
flag used to do — it prevents double-counting — but without ever having
to rewrite anything on `TrustEvidence`.

### 2.4 Evidence Types

```python
class EvidenceType(StrEnum):
    INCIDENT_IMPACT = "incident_impact"
    RECOVERY_PROGRESS = "recovery_progress"
    SUSTAINED_PERIOD = "sustained_period"
    MANUAL_REVIEW = "manual_review"          # see 2.9 — heavily restricted
```

### 2.5 Disputing Evidence (New Structure, Part of Point 6)

Because `TrustEvidence` cannot be edited, "disputing" it must also be a
new row, not a modification:

```python
@dataclass(frozen=True)
class TrustEvidenceDispute:
    id: str
    evidence_id: str
    created_at: datetime
    reason: str
    disputed_by: str            # 'user' | 'keyholder_review'
```

Disputed evidence remains unchanged in history (it really was written,
and it really was part of how the system reasoned at that moment — that
is itself an audit-relevant fact). A dispute **excludes the evidence from
future recalculations** (see TI4b), but does not erase whatever influence
it may already have had in past `TrustRecalculation` records. Correcting
an earlier influence is done exclusively via a compensating record (see
2.9).

### 2.6 Score Recalculation

```python
@dataclass(frozen=True)
class TrustRecalculation:
    id: str
    domain_id: str
    created_at: datetime

    previous_score: float
    new_score: float
    previous_confidence: float          # new — confidence is now recalculated by the same mechanism (see 3.5)
    new_confidence: float

    triggered_by: str                   # 'incident' | 'window_completion' | 'scheduled_review' | 'manual'
    explanation: str                    # REQUIRED
    # contributing_evidence_ids is no longer duplicated as a field here —
    # the source of truth is TrustRecalculationEvidence (2.3), see TI10b
```

### 2.7 Overall Trust

Unchanged from v1 — a purely descriptive report, no authoritative
`score` field (see Section 4).

### 2.8 Confirmation — a New Layer (Fix for Point 3)

```python
class IncidentConfirmation(StrEnum):
    UNCONFIRMED = "unconfirmed"
    PROVISIONAL = "provisional"
    CONFIRMED = "confirmed"

class ConfirmationSource(StrEnum):
    SYSTEM_VERIFIED = "system_verified"        # independently verified by the system
    USER_ACKNOWLEDGED = "user_acknowledged"     # the user confirmed/admitted it themselves
    KEYHOLDER_REVIEW = "keyholder_review"       # confirmed during manual review

@dataclass(frozen=True)
class ConfirmationRecord:
    """Append-only. The only way an Incident advances between confirmation levels."""
    id: str
    incident_id: str
    created_at: datetime
    previous_confirmation: IncidentConfirmation
    new_confirmation: IncidentConfirmation
    source: ConfirmationSource
    evidence_description: str           # REQUIRED — what the advancement was based on
```

`Incident.confirmation` (see 2.10) is a denormalization of the most
recent `ConfirmationRecord.new_confirmation` — the same pattern as
`TrustDomainState.score` versus `TrustRecalculation`.

### 2.9 Manual Review — Restricted (Fix for Point 6)

`MANUAL_REVIEW` evidence **must not carry an arbitrary, manually entered
`raw_weight`**. Instead, three separate, narrow operations exist:

```python
def correct_incident_classification(db, incident_id: str, corrected_evidence: IncidentEvidence, reason: str) -> None:
    """
    Corrects the STRUCTURED FACTS about an Incident (not the Trust
    impact directly). Creates a new ConfirmationRecord/IncidentEvidence
    revision and re-runs the SAME deterministic assess_severity() rubric
    over the corrected facts. The resulting raw_weight comes from the
    rubric, not from manual entry.
    """

def dispute_evidence(db, evidence_id: str, reason: str, disputed_by: str) -> None:
    """Creates a TrustEvidenceDispute (2.5). Changes nothing else."""

def record_compensating_evidence(db, original_evidence_id: str, domain_id: str, raw_weight: float, explanation: str) -> TrustEvidence:
    """
    The only way to "take back" an earlier impact is to write NEW
    evidence with the opposite sign, explicitly referencing the original
    evidence in its `explanation` and `source_entity_id` (set to
    original_evidence_id, evidence_type=MANUAL_REVIEW). History remains
    complete and legible — we see both the original error and the
    correction, not just the "corrected" end result.
    """
```

None of these three functions allows writing `raw_weight` as a bare
number — "add/remove X points" — without a tie to the rubric or to an
explicit compensating relationship with existing evidence (TI14).

### 2.10 Incident and Severity — Splitting Intrinsic/Cooperation/Confirmation (Fix for Points 3, 4, 5)

```python
class ImpactLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

class IntentAssessment(StrEnum):
    UNINTENTIONAL = "unintentional"
    UNCLEAR = "unclear"                 # DEFAULT
    DELIBERATE = "deliberate"

class BreachDirectness(StrEnum):
    INDIRECT = "indirect"
    PARTIAL = "partial"
    DIRECT = "direct"

class EvidenceConfidenceLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

@dataclass(frozen=True)
class RepetitionEvidence:
    """Replaces the boolean `repetition` (fix for Point 5)."""
    same_rule_confirmed_count: int          # ONLY Incidents with confirmation=CONFIRMED are counted
    evaluation_window_days: int
    source_incident_ids: tuple[str, ...]    # may include incidents that were ALREADY CONSUMED
                                             # (see the repetition invariant below — historical
                                             #  evidence of a pattern ≠ re-consuming the incident)

@dataclass(frozen=True)
class IncidentEvidence:
    actual_or_potential_impact: ImpactLevel
    intentionality: IntentAssessment = IntentAssessment.UNCLEAR
    rule_breach_directness: BreachDirectness
    repetition: RepetitionEvidence
    evidence_confidence: EvidenceConfidenceLevel

@dataclass(frozen=True)
class CooperationAssessment:
    """Separate from IncidentEvidence — cooperation is NOT a property of
    the incident itself, it is a property of the RESPONSE to it (fix for
    Point 4)."""
    self_disclosed: bool                    # the user actively reported it themselves
    active_cooperation_in_resolution: bool  # cooperated in resolving/remedying it
    notes: str | None = None

class SeverityTier(StrEnum):
    MINOR = "minor"
    MODERATE = "moderate"
    MAJOR = "major"
    CRITICAL = "critical"

@dataclass(frozen=True)
class IncidentAssessment:
    intrinsic_severity: SeverityTier        # DOES NOT DEPEND on cooperation or confirmation
    confirmation: IncidentConfirmation
    cooperation: CooperationAssessment
    evidence: IncidentEvidence
    rubric_explanation: str
```

```python
@dataclass
class Incident:
    id: str
    created_at: datetime
    rule_group_id: str
    trust_domain: str
    confirmation: IncidentConfirmation      # denormalized from the latest ConfirmationRecord
    assessment: IncidentAssessment | None   # None until confirmation reaches CONFIRMED (see the confirmation invariant)
    description: str
    consumed_by_penalty_window_id: str | None = None
```

`Incident.assessment` is `None` until the Incident reaches the level of
confirmation required for classification (see 5.1). This is a type-level
guarantee — code that attempts to read `severity_tier` from an
unconfirmed Incident gets `None` and must handle that explicitly, rather
than silently working with an invalid value.

---

## 3. Rules for Updating Domain Trust

### 3.1 Data Flow (Updated)

```
Incident is created as UNCONFIRMED
      |
      v
[ConfirmationRecord — advances to PROVISIONAL/CONFIRMED, see 5.1]
      |  (as long as confirmation < CONFIRMED, the flow stops here)
      v
assess_severity(evidence)  --->  IncidentAssessment.intrinsic_severity
      |
      v
TrustEvidence (evidence_type=INCIDENT_IMPACT) -- append-only, DOES NOT CHANGE
      |
      v
[Recalculation trigger]
      |
      v
TrustRecalculation + TrustRecalculationEvidence (consumes the evidence, UNIQUE)
      |
      v
TrustDomainState.score/confidence updated (same transaction)
```

### 3.2 Recalculation Triggers

Unchanged from v1, with the refinement from decisions 3/6:

| Trigger | When | Note |
|---|---|---|
| `incident` | immediately after `INCIDENT_IMPACT` evidence is created, but **only for `confirmation=CONFIRMED`** | unconfirmed/provisional evidence never affects the score at all (decision 3) |
| `window_completion` | completion of a Penalty Window | aggregates `RECOVERY_PROGRESS` evidence for the window |
| `scheduled_review` | periodic (config, weekly by default) | processes `SUSTAINED_PERIOD` evidence **if it was generated** (see 3.7) and a staleness-driven confidence recalculation; does not by itself generate positive evidence merely because time has passed (decision 6) |
| `manual` | Keyholder/user | only via `correct_incident_classification()`, never direct entry |

### 3.3 Confidence Weighting

Unchanged from v1 (`effective_weight = raw_weight * evidence_confidence`,
capped below a threshold) — `effective_weight` is now written to
`TrustRecalculationEvidence.effective_weight` at the moment of
consumption, rather than onto `TrustEvidence` itself.

### 3.4 Default Values for a New Domain (Addition for Point 7)

```python
DEFAULT_NEW_DOMAIN_SCORE = 0.6         # parameter, critical_change
DEFAULT_NEW_DOMAIN_CONFIDENCE = 0.15   # parameter, critical_change
```

`0.6` is a deliberately chosen midpoint — neither `1.0` (unearned full
autonomy with no evidence) nor `0.0` (unearned distrust with no
evidence). The low `0.15` confidence immediately signals that this
number is weakly supported until evidence accumulates. The specific
values are a parameter — they can change, but only via a
`critical_change` (decision 2), never silently.

When a domain is created, `initial_score_override`/
`initial_confidence_override` (2.1) may be specified for cases where a
domain is introduced with pre-existing context (e.g., migration from
another system) — this too is part of the approved consent request, not
a runtime decision.

### 3.5 Score Transition Invariants (Addition for Point 7)

```python
MAX_ABSOLUTE_DELTA_PER_RECALCULATION = 0.15   # parameter, critical_change

def apply_recalculation(previous_score: float, proposed_delta: float) -> float:
    """
    new_score is ALWAYS in [0.0, 1.0] (clamped).
    |new_score - previous_score| <= MAX_ABSOLUTE_DELTA_PER_RECALCULATION,
    regardless of how large the sum of effective_weight of the input
    evidence is.
    """
    bounded_delta = max(-MAX_ABSOLUTE_DELTA_PER_RECALCULATION,
                         min(MAX_ABSOLUTE_DELTA_PER_RECALCULATION, proposed_delta))
    return max(0.0, min(1.0, previous_score + bounded_delta))
```

Direct consequences of this invariant (no additional logic is needed):

- **A single ordinary Incident cannot destroy an entire domain** — even a
  `CRITICAL` Incident moves the score by at most
  `MAX_ABSOLUTE_DELTA_PER_RECALCULATION` in one recalculation. A
  repeated pattern (see `RepetitionEvidence`) will lower the score
  further, but across several separate, audited steps — not in one
  blow.
- **A single Recovery Plan cannot automatically restore full Trust** —
  for the same reason, in the opposite direction.
- **Recovery is genuinely achievable** — the cap is symmetric (the same
  `MAX_ABSOLUTE_DELTA_PER_RECALCULATION` in both directions), unless
  `philosophy.md` 2.7/3.1 opts for an explicit, justified asymmetry as
  its own, named parameter — see v1 Section 3.4; the principle stands.

### 3.6 Confidence — How It Arises and Is Updated (Addition for Point 7)

Confidence is not an independently stored number that "just is" — it is
a **value derived from the volume and freshness of applied evidence**
within a rolling window, recalculated at every `TrustRecalculation`
(which is why it has its own `previous_confidence`/`new_confidence`
fields, 2.6):

```python
CONFIDENCE_ROLLING_WINDOW_DAYS = 180    # parameter

def compute_confidence(applied_evidence_in_window: list[TrustEvidence]) -> float:
    """
    A diminishing-returns function of the volume of evidence within the
    rolling window — more independent evidence means higher confidence,
    but with a shrinking increment. Explicit, deterministic, not hidden
    weighting.

    Example shape (the exact constant k is a parameter to be tuned):
        confidence = 1 - exp(-k * len(applied_evidence_in_window))
    """
```

Because this is a rolling window, confidence naturally decreases when old
evidence "drops out" of the window without being replaced by new
evidence — but this change only takes effect when a recalculation runs
(any recalculation, even with `delta_score=0`), not as a silent
computation on every read (consistent with Principle 2.8/TI11 from v1).
`scheduled_review` is the trigger that guarantees this even during
periods with no new evidence.

**Low confidence means the score's authority is limited, not
automatically that the score is low** — this is ensured by
`compute_confidence()` being an entirely independent function of `score`;
it reads only the volume/freshness of evidence, never the score value
itself.

### 3.7 SUSTAINED_PERIOD Requires Documented Exposure (Fix for Point 8)

```python
@dataclass(frozen=True)
class ExposureRecord:
    """Input to deciding whether SUSTAINED_PERIOD evidence is created at all."""
    domain_id: str
    period_start: datetime
    period_end: datetime
    opportunity_count: int              # how many relevant situations occurred
    successful_observation_count: int   # how many of them were observably fine
    monitoring_coverage: float          # 0.0–1.0, how much of the period could actually be observed

MIN_MONITORING_COVERAGE_FOR_SUSTAINED_PERIOD = 0.6   # parameter

def maybe_create_sustained_period_evidence(exposure: ExposureRecord) -> TrustEvidence | None:
    """
    Returns None (no evidence is created) if:
      - opportunity_count == 0 (there was no relevant situation to demonstrate anything), OR
      - monitoring_coverage < MIN_MONITORING_COVERAGE_FOR_SUSTAINED_PERIOD.
    "We have no recorded Incident" is NEVER sufficient by itself — that
    is exactly the difference between the absence of evidence and
    evidence of absence.
    """
```

The `scheduled_review` process must first assemble the `ExposureRecord`
(from check-ins, the frequency of situations relevant to the given rule,
etc.) — if it lacks such grounding, it creates no positive evidence at
all, only a confidence recalculation (3.6) with no new evidence.

---

## 4. Overall Trust

Unchanged from v1 — purely descriptive; `concerning_pattern_detected` is
a configurable heuristic (default: "≥2 active domains with
`trend=declining` simultaneously within a defined period"), never an
authoritative score, never an input to restriction decisions (TI7).

---

## 5. Severity and Confirmation Model

### 5.1 Confirmation Gating (Fix for Point 3)

```python
def register_incident_report(db, evidence: IncidentEvidence, domain_id: str, rule_group_id: str) -> Incident:
    """A new Incident ALWAYS starts as UNCONFIRMED. assess_severity() is
    NOT YET called for it — Incident.assessment stays None."""

def confirm_incident(db, incident_id: str, new_confirmation: IncidentConfirmation, source: ConfirmationSource, evidence_description: str) -> None:
    """
    The only way an Incident advances. Writes a ConfirmationRecord
    (append-only) and updates Incident.confirmation (denormalized).

    If new_confirmation == CONFIRMED, ONLY THEN is assess_severity()
    called, producing an IncidentAssessment and TrustEvidence.
    """
```

Consequences (directly per the requirements):

- `UNCONFIRMED`/`PROVISIONAL` may create an `ObservationRecord` or a
  review request for the Coach/Keyholder — but **never** `TrustEvidence`,
  **never** a `consumed_by_penalty_window_id`, **never** an input into
  `should_extend()` (in the next step).
- Advancing between levels is always an explicit `ConfirmationRecord`
  with `evidence_description` populated — never an automatic advance
  just because the LLM's interpretation "sounds more certain."
- `USER_ACKNOWLEDGED` (the user admits it themselves) is a legitimate
  `ConfirmationSource` that leads directly to `CONFIRMED` — this is
  effectively a formalization of "self_disclosed," now as a typed
  confirmation path, not as a factor that lowers severity (see 5.2).

### 5.2 Intrinsic Severity — Separated From Cooperation (Fix for Point 4)

```python
def assess_severity(evidence: IncidentEvidence) -> SeverityTier:
    """
    Called ONLY for Incidents with confirmation=CONFIRMED (5.1). The
    signature MUST NOT accept trust_score, TrustDomainState, or
    CooperationAssessment — cooperation is a separate axis (see below);
    the intrinsic_severity of two Incidents with identical impact and
    circumstances must come out the same, whether they were self-reported
    or discovered.
    """
    score = 0
    score += {ImpactLevel.LOW: 0, ImpactLevel.MEDIUM: 1, ImpactLevel.HIGH: 2}[evidence.actual_or_potential_impact]
    score += {IntentAssessment.UNINTENTIONAL: 0, IntentAssessment.UNCLEAR: 0, IntentAssessment.DELIBERATE: 1}[evidence.intentionality]
    score += {BreachDirectness.INDIRECT: 0, BreachDirectness.PARTIAL: 1, BreachDirectness.DIRECT: 2}[evidence.rule_breach_directness]
    score += _repetition_contribution(evidence.repetition)   # see below

    tiers = [SeverityTier.MINOR, SeverityTier.MODERATE, SeverityTier.MAJOR, SeverityTier.CRITICAL]
    return tiers[min(score // 2, len(tiers) - 1)]


def _repetition_contribution(rep: RepetitionEvidence) -> int:
    """same_rule_confirmed_count counts ONLY CONFIRMED Incidents (5.1)."""
    if rep.same_rule_confirmed_count <= 1:
        return 0
    if rep.same_rule_confirmed_count <= 3:
        return 1
    return 2
```

The concrete weights (decision 2 — conservative, explicit, protected by
`critical_change`):

| Factor | Value → Points |
|---|---|
| `actual_or_potential_impact` | LOW=0, MEDIUM=1, HIGH=2 |
| `intentionality` | UNINTENTIONAL=0, UNCLEAR=0, DELIBERATE=1 |
| `rule_breach_directness` | INDIRECT=0, PARTIAL=1, DIRECT=2 |
| `repetition` (`same_rule_confirmed_count`) | ≤1→0, 2–3→1, ≥4→2 |
| **Tier mapping** (total score `// 2`) | 0→MINOR, 1→MODERATE, 2→MAJOR, 3+→CRITICAL |

This table is, from now on, a **`critical_change` parameter**, not a
constant freely adjustable during refactoring — any change to the
weights or thresholds requires a `ConsentRecord`, exactly like a rule
change (decision 2). Neither the runtime nor the LLM has any path to
"tune" the table on its own.

Note: `evidence_confidence` **no longer caps** `intrinsic_severity` at
MINOR (that was the v1 solution, which the fix for Point 3 replaces with
a cleaner mechanism — low certainty now prevents an Incident from
reaching `CONFIRMED` at all, so `assess_severity()` is never called for
it). `evidence_confidence` remains relevant to `effective_weight` (3.3)
and to the decision inside `confirm_incident()` about whether the basis
is strong enough for `CONFIRMED`.

### 5.3 Cooperation — a Positive Factor Outside Intrinsic Severity (Fix for Point 4)

```python
def cooperation_trust_offset(cooperation: CooperationAssessment) -> float:
    """
    A separate, small positive adjustment to effective_weight (3.3) —
    NEVER to intrinsic_severity. Active cooperation softens the IMPACT
    on Trust, not the classification of the event itself.
    """
    offset = 0.0
    if cooperation.self_disclosed:
        offset += COOPERATION_SELF_DISCLOSURE_OFFSET   # parameter, a small positive number
    if cooperation.active_cooperation_in_resolution:
        offset += COOPERATION_ACTIVE_RESOLUTION_OFFSET  # parameter
    return offset
```

`cooperation_trust_offset()` is applied when computing the `raw_weight`
written into `TrustEvidence` (it softens a negative `raw_weight`; it
never flips it to positive for a genuinely serious Incident).
`CooperationAssessment` is also available as a read-only input to the
future `should_extend()` (via `ExtensionContext`, see Section 6 below)
and to the Coach when building a Recovery Plan — but never to
`assess_severity()`.

### 5.4 Single Use of an Incident — Confirmed for Repetition Too (Addition for Point 5)

`RepetitionEvidence.source_incident_ids` may contain Incidents that were
already `consumed_by_penalty_window_id` for an earlier, closed window.
This is intentional and does not conflict with the single-use rule
(`philosophy.md` 3.8):

- **Use as a consumed Incident** (grounds for starting/extending a
  specific Penalty Window) is governed by
  `consumed_by_penalty_window_id` — this remains exactly once during the
  lifetime of an Incident, unchanged.
- **Use as historical evidence of a pattern for Trust** (an entry in
  `source_incident_ids`) is a read-only, non-destructive reference — it
  consumes nothing; it merely says "this is not the first time." A
  read-only reference to an already-consumed Incident does not disturb
  its state in any way.

At the type level, this distinction is enforced by `RepetitionEvidence`
being a read-only DTO (a frozen dataclass assembled by a query), not a
path that could write to `consumed_by_penalty_window_id`.

---

## 6. Explicit Restriction of Outputs for `should_extend()` (Fix for Point 2)

This is **not** a design of `should_extend()` itself (that is still the
next step) — it is a commitment about the shape of the interface the
Trust Manager exposes toward it, so that the input for the next design is
already stable now:

```python
@dataclass(frozen=True)
class ExtensionContext:
    """
    A PRELIMINARY shape — the exact fields will be finalized when
    should_extend() is designed. The key property this document
    GUARANTEES: this is the ONLY type allowed as input to
    should_extend(). It does not, and must not, contain
    TrustDomainState, trust_score, confidence, trend, or
    OverallTrustReport in any form — neither wrapped nor derived.
    """
    same_rule_confirmed_incident_count_in_current_window: int
    extension_hours_already_assigned: float
    remaining_active_hour_capacity: float
    occurred_during_recovery_task: bool
    intrinsic_severity: SeverityTier          # from the CURRENT incident, not from Trust history
    cooperation: CooperationAssessment        # from the CURRENT incident
```

The Trust Manager therefore never supplies `should_extend()` with
anything beyond this — no score, no `confidence`, no `trend`. Low Trust
cannot influence an Extension, because `should_extend()` **has no access
to it** — not merely because it "shouldn't use it" as a convention.

---

## 7. Technical Invariants (Updated)

| # | Source | Invariant |
|---|---|---|
| TI1 | 3.7, 2.5 | The domain registry (creation/deactivation/reactivation) always requires `*_via_consent_id`. Reactivation is its own `critical_change`, not a silent flip of `is_active` back to true (decision 7). |
| TI2 | 2.8 | `TrustDomainState.score`/`confidence` change exclusively as a side effect of writing a new `TrustRecalculation` within the same transaction. |
| TI3 | 2.8, fix for Point 1 | `TrustEvidence` has NO field that changes after creation. The access layer provides no `UPDATE`/`DELETE` for this table at all. |
| TI4 | 2.2, 2.8 | `TrustRecalculationEvidence.evidence_id` has a `UNIQUE` constraint — a piece of evidence is consumed at most once. |
| TI4b | fix for Point 6 | Disputed evidence (`TrustEvidenceDispute`) is excluded from ALL future `TrustRecalculationEvidence` writes (checked when selecting input evidence for a recalculation), but historical writes remain unchanged. |
| TI5 | v1 requirements | `assess_severity()` must not accept `trust_score`, `TrustDomainState`, or `CooperationAssessment` (the fix for Point 4 extends the original invariant). |
| TI6 | fix for Point 2 | `should_extend()` (next step) may accept exclusively `ExtensionContext` (Section 6) — no other type carrying Trust score/confidence/trend/Overall Trust may appear in its signature. |
| TI7 | 3.7 | No restriction/privilege function may have `OverallTrustReport` in its signature. |
| TI8 | v1 requirements | `RECOVERY_PROGRESS` evidence is written immediately, but is only reflected in the score via the `window_completion`/`scheduled_review` trigger. |
| TI9 | v1 requirements | `effective_weight = raw_weight * evidence_confidence`, with threshold capping. |
| TI10 | 2.6, 2.8 | Every `TrustRecalculation` has a non-empty `explanation`. |
| TI10b | fix for Point 1 | Every `TrustRecalculation` must have at least one corresponding `TrustRecalculationEvidence` row (except for a purely staleness-driven confidence recalculation with no new evidence, which may reference evidence still valid within the window — see 3.6). |
| TI11 | v1 requirements | Confidence/score is never computed silently on read — always only via `TrustRecalculation`. |
| TI12 | 2.8 | The state change and the corresponding `domain_event` are created within the same transaction. |
| TI13 | 3.9, 3.10 | Mandatory Hygiene/Health Access, an approved exemption, a Freeze, or an Emergency Override never generate `TrustEvidence` of type `INCIDENT_IMPACT`. |
| TI14 | fix for Point 6 | No code path writes to `TrustEvidence.raw_weight` from free-form input (a manually entered number or a direct LLM output) — always only the output of the `assess_severity()` rubric, or an explicit `record_compensating_evidence()` tied to the original evidence. |
| TI15 | fix for Point 3 | `assess_severity()` is called exclusively for `Incident.confirmation == CONFIRMED`. For `UNCONFIRMED`/`PROVISIONAL`, `Incident.assessment` remains `None`. |
| TI16 | fix for Point 3 | An `IncidentConfirmation` advance is created exclusively via a `ConfirmationRecord` with `evidence_description` populated — never as a side effect of another operation. |
| TI17 | fix for Point 5 | `RepetitionEvidence.same_rule_confirmed_count` counts only Incidents with `confirmation=CONFIRMED`. |
| TI18 | fix for Point 8 | `SUSTAINED_PERIOD` evidence is created only if `ExposureRecord.opportunity_count > 0` and `monitoring_coverage >= MIN_MONITORING_COVERAGE_FOR_SUSTAINED_PERIOD`. |
| TI19 | fix for Point 7 | `|new_score - previous_score| <= MAX_ABSOLUTE_DELTA_PER_RECALCULATION` always holds, without exception (even for `CRITICAL` severity). `new_score` is always clamped to `[0.0, 1.0]`. |
| TI20 | fix for Point 7 | The `assess_severity()` weight table (5.2) and `MAX_ABSOLUTE_DELTA_PER_RECALCULATION`/the new-domain default values are `critical_change` parameters — a change requires a `ConsentRecord`; neither the runtime nor the LLM may adjust them independently. |

---

## 8. Domain Events (Updated)

| event_type | source_module | When It Occurs |
|---|---|---|
| `trust_domain.created` | trust_manager | a new domain is approved via consent |
| `trust_domain.deactivated` | trust_manager | a domain is deactivated via consent |
| `trust_domain.reactivated` | trust_manager | a domain is reactivated via new consent (TI1) |
| `incident.reported` | trust_manager | a new Incident, always `UNCONFIRMED` |
| `incident.confirmation_changed` | trust_manager | a new `ConfirmationRecord` |
| `trust_evidence.recorded` | trust_manager | any new `TrustEvidence` row |
| `trust_evidence.disputed` | trust_manager | a new `TrustEvidenceDispute` |
| `trust_domain.recalculated` | trust_manager | a new `TrustRecalculation` (even when `delta=0`) |
| `overall_trust.report_generated` | trust_manager | a new `OverallTrustReport` is generated |

---

## 9. Transaction Boundaries (Updated)

```python
def _record_incident_report(db: Database, incident: Incident, event: DomainEvent) -> None:
    with db._connect() as conn:
        _insert_incident(conn, incident)   # confirmation=UNCONFIRMED
        _write_event(conn, event)          # incident.reported

def _apply_confirmation(db: Database, record: ConfirmationRecord, updated_incident: Incident, event: DomainEvent) -> None:
    with db._connect() as conn:
        _insert_confirmation_record(conn, record)
        _update_incident_confirmation(conn, updated_incident)   # denormalization
        _write_event(conn, event)          # incident.confirmation_changed
        # If updated_incident.confirmation == CONFIRMED, the calling code
        # (outside this transaction, or as a continuation of it) then
        # runs assess_severity() and _record_evidence().

def _record_evidence(db: Database, evidence: TrustEvidence, event: DomainEvent) -> None:
    with db._connect() as conn:
        _insert_trust_evidence(conn, evidence)   # append-only insert, nothing else
        _write_event(conn, event)                # trust_evidence.recorded

def _apply_recalculation(
    db: Database,
    recalculation: TrustRecalculation,
    consumed_evidence_ids: list[str],
    event: DomainEvent,
) -> None:
    with db._connect() as conn:
        _insert_recalculation(conn, recalculation)
        for evidence_id in consumed_evidence_ids:
            _insert_recalculation_evidence(conn, recalculation.id, evidence_id)  # UNIQUE(evidence_id) - TI4
        _update_domain_state(conn, recalculation.domain_id, recalculation.new_score, recalculation.new_confidence)
        _write_event(conn, event)   # trust_domain.recalculated
```

Each of these four operations is a separate, atomic unit. Real time
typically passes between `_record_incident_report` and
`_apply_confirmation` (review, further conversation) — that is fine,
because in the meantime the Incident exists in a consistent, clearly
named state (`UNCONFIRMED`), not in limbo.

---

## 10. Example Scenarios (Updated)

**Scenario A — an unconfirmed suspicion never affects Trust.**
The Coach records an ambiguous signal in a conversation (e.g., a remark
that could, but need not, indicate a violation). `register_incident_report()`
creates `Incident(confirmation=UNCONFIRMED, assessment=None)`. An
`ObservationRecord` is created with `flagged_for_review=True`. No
`TrustEvidence` is generated; `assess_severity()` is not called. Three
days later, the user confirms in conversation what happened —
`confirm_incident(source=USER_ACKNOWLEDGED)` advances it to `CONFIRMED`,
and only now does an `IncidentAssessment` and `TrustEvidence` get
created.

**Scenario B — the same incident, two different cooperation paths, the same intrinsic_severity.**
Two separate Incidents share the same `impact=MEDIUM`,
`intentionality=UNCLEAR`, `breach_directness=DIRECT`,
`repetition.same_rule_confirmed_count=1`. The first has
`self_disclosed=True`; the second has `self_disclosed=False`. Both go
through `assess_severity()` and come out at the **same**
`intrinsic_severity` (MODERATE, per the table in 5.2 — impact 1 +
directness 1 + repetition 0 = 2 → MODERATE). The difference shows up only
in `effective_weight` via `cooperation_trust_offset()` — the first has a
smaller negative impact on Trust than the second, but the severity
classification is identical.

**Scenario C — repetition counts only confirmed incidents, but history remains readable.**
In the `hygiene` domain, two earlier `CONFIRMED` Incidents already exist
(both long since consumed by a closed Penalty Window) plus one new,
just-confirmed one. `RepetitionEvidence(same_rule_confirmed_count=3,
evaluation_window_days=30, source_incident_ids=(old1, old2, new))`.
`_repetition_contribution()` returns `1` (because `3 <= 3`). old1 and
old2's `consumed_by_penalty_window_id` does not change — they are only
read as evidence of a pattern, not re-consumed.

**Scenario D — correcting a classification error without editing history.**
Keyholder review finds that an Incident's `intentionality` was
incorrectly assessed as `DELIBERATE`; it should have been
`UNINTENTIONAL`. `correct_incident_classification()` creates a new
`IncidentEvidence` version, re-runs `assess_severity()` (which now
produces a lower tier), and, because the original (erroneous)
`TrustEvidence` may already have been consumed in a
`TrustRecalculation`, `record_compensating_evidence()` creates a
positive `raw_weight`, explicitly referencing the original evidence in
its `explanation`. History shows both — the original (erroneous)
handling and the correction — not just the resulting "corrected" state
with no trace.

**Scenario E — SUSTAINED_PERIOD denied due to insufficient coverage.**
`scheduled_review` assembles an `ExposureRecord` for the `routine`
domain covering the past week: `opportunity_count=5`, but
`monitoring_coverage=0.3` (the user's check-in system was disconnected
for several days). Because `0.3 < MIN_MONITORING_COVERAGE_FOR_SUSTAINED_PERIOD
(0.6)`, no `SUSTAINED_PERIOD` evidence is created. Only a confidence
recalculation runs (no new evidence, the staleness effect from 3.6) —
the score does not change, but `confidence` may drop slightly, because
less fresh evidence is available.

**Scenario F — the per-recalculation cap prevents "destroying" a domain.**
A new `CRITICAL` incident occurs in a domain currently at `score=0.5`.
Even if the theoretical `raw_weight` corresponded to a drop of `0.4`,
`apply_recalculation()` clips it to `MAX_ABSOLUTE_DELTA_PER_RECALCULATION
= 0.15` → the new score is `0.35`, not `0.1`. Repeating an equally
severe pattern a second and third time (through separate, audited
recalculations) will lower the score further — but not in a single
blow.

---

## 11. Trust Manager Test Matrix

| # | Scenario | Given | When | Then |
|---|---|---|---|---|
| TT1 | Append-only evidence cannot be modified | existing `TrustEvidence` | attempt an `UPDATE`/`DELETE` via the access layer | the method does not exist — it must fail at the API level, not merely by convention (TI3) |
| TT2 | Evidence is consumed at most once | evidence used in `TrustRecalculation` A | attempt to include it in `TrustRecalculation` B | `UNIQUE(evidence_id)` on `TrustRecalculationEvidence` rejects it (TI4) |
| TT3 | An unconfirmed Incident does not affect Trust | `Incident(confirmation=UNCONFIRMED)` | any amount of time passes, no action | `TrustDomainState.score` unchanged, no `TrustEvidence` arising from this Incident (TI15) |
| TT4 | Provisional also does not affect Trust | `confirm_incident(new_confirmation=PROVISIONAL)` | check `Incident.assessment` | remains `None` (TI15) |
| TT5 | Confirmation requires an explicit record | Incident `UNCONFIRMED` | attempt to set `confirmation=CONFIRMED` without a `ConfirmationRecord` | impossible — the only path is `confirm_incident()`, which always writes a `ConfirmationRecord` (TI16) |
| TT6 | Repetition counts only CONFIRMED | 2× `CONFIRMED` + 1× `UNCONFIRMED` Incident of the same rule | query `RepetitionEvidence` | `same_rule_confirmed_count = 2`, not 3 (TI17) |
| TT7 | A historical, consumed Incident as repetition evidence | Incident from a closed Penalty Window, `consumed_by_penalty_window_id != NULL` | included in the `source_incident_ids` of a new `RepetitionEvidence` | allowed, `consumed_by_penalty_window_id` is not changed (5.4) |
| TT8 | Manual review cannot write an arbitrary raw_weight | — | attempt to call a nonexistent "set raw_weight" API | no such method exists; the only paths are `correct_incident_classification`/`dispute_evidence`/`record_compensating_evidence` (TI14) |
| TT9 | A correction is a compensating record, not an edit | erroneous evidence E1 already consumed in recalculation R1 | `record_compensating_evidence(original=E1)` | a new evidence E2 is created with the opposite `raw_weight`; E1 remains unchanged; both are visible in history (TI3, TI14) |
| TT10 | Score never falls outside [0,1] | `previous_score=0.05`, an extremely negative `proposed_delta` | `apply_recalculation()` | `new_score` is clamped to `0.0`, not negative (TI19) |
| TT11 | Max delta per recalculation | `CRITICAL` Incident, theoretical delta > cap | recalculation | the actual change is `<= MAX_ABSOLUTE_DELTA_PER_RECALCULATION` (TI19, Scenario F) |
| TT12 | Confidence grows with the volume of evidence | domain with 1 applied evidence entry | 5 more independent evidence entries arrive within the window | `confidence` grows, but with diminishing increments (3.6) |
| TT13 | SUSTAINED_PERIOD denied without exposure | `ExposureRecord.opportunity_count=0` | `scheduled_review` | no `SUSTAINED_PERIOD` evidence is created (TI18, Scenario E) |
| TT14 | SUSTAINED_PERIOD denied with low coverage | `monitoring_coverage=0.3 < 0.6` | `scheduled_review` | no evidence, only a confidence recalculation (TI18) |
| TT15 | The weight table is a critical_change | attempt to change values in the Section 5.2 table outside the consent flow | — | rejected / impossible without a `ConsentRecord` (TI20) |
| TT16 | Cooperation does not affect intrinsic_severity | two Incidents, identical `IncidentEvidence`, different `CooperationAssessment` | `assess_severity()` on both | identical `intrinsic_severity` (TI5, Scenario B) |
| TT17 | Cooperation affects effective_weight | the same two Incidents as TT16 | compute `raw_weight` with `cooperation_trust_offset()` | different `effective_weight` (lower negative impact for the cooperating one) |
| TT18 | Mandatory/Exemption/Override generate no evidence | an approved exemption or an Emergency Override | check `TrustEvidence` created at this moment | none (TI13) |
| TT19 | A dispute excludes evidence from future recalculations | evidence E1 disputed (`TrustEvidenceDispute`) | another `TrustRecalculation` runs | E1 is not among the input evidence of the new recalculation (TI4b) |
| TT20 | should_extend() never receives Trust state | (preparation for the next step) | static check of the `ExtensionContext` signature | contains no `TrustDomainState`/`score`/`confidence`/`trend`/`OverallTrustReport` in any form (TI6) |
| TT21 | Overall Trust never enters restriction decisions | any `OverallTrustReport` | check the signatures of privilege/hygiene/lock functions | none of them accept `OverallTrustReport` (TI7) |
| TT22 | A new domain has an explicit default | a domain created without an override | read `TrustDomainState` | `score=DEFAULT_NEW_DOMAIN_SCORE`, `confidence=DEFAULT_NEW_DOMAIN_CONFIDENCE` (3.4) |
| TT23 | Reactivation requires new consent | a deactivated domain | attempt to set `is_active=True` without a new `ConsentRecord` | rejected (TI1, decision 7) |
| TT24 | Deactivation preserves history | a domain with evidence/recalculation history, deactivated | read the history after deactivation | all history remains readable unchanged; only new writes are blocked |

---

## 12. Summary of Review Decisions (for Future Audit)

For clarity — all 7 open questions from v1 were resolved directly in your
review, not by me:

1. Discrete `SeverityTier` scale — confirmed.
2. Rubric weights — a conservative, explicit table (5.2), `critical_change`.
3. Recalculation trigger for an incident — immediate, but only for `CONFIRMED`.
4. `concerning_pattern_detected` — a configurable heuristic, informational only.
5. Who populates `IncidentEvidence` — a separate follow-up design; the LLM must not confirm on its own.
6. `scheduled_review` — weekly default; does not generate evidence merely because time has passed.
7. Domain deactivation — history is preserved; reactivation = a new `critical_change`.

The document is now ready for final approval. It will be followed by the
design of `should_extend()`, with `ExtensionContext` (Section 6) as the
only permitted input type carrying anything from the Trust Manager.

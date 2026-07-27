"""
penalty_engine/extension.py

The should_extend() three-stage algorithm (Eligibility -> Base Magnitude
-> Mitigation -> Capacity Cap). Canonical:
docs/architecture/extension_technical_design.md. Pure functions, no
database access -- mirrors trust_manager/severity.py and
trust_manager/recalculation.py's own separation of computation from
persistence.

Four parameter groups are explicitly marked TBD in the architecture
document itself (Section 10) -- not architectural questions, numeric
placeholders. The values below are this slice's own defaults, flagged
here exactly as trust_manager/recalculation.py flags
MAX_ABS_EFFECTIVE_WEIGHT/CONFIDENCE_K and penalty_engine/window.py flags
DEFAULT_BASE_DURATION_HOURS. See penalty_engine/README.md for the full
list and reasoning.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from trust_manager.models import CooperationAssessment, SeverityTier

__all__ = [
    "ExtensionEligibilityReason",
    "ExtensionContext",
    "ExtensionDecision",
    "determine_extension_eligibility",
    "calculate_base_magnitude",
    "apply_mitigation",
    "apply_capacity_cap",
    "should_extend",
]


def new_id() -> str:
    return str(uuid.uuid4())


class ExtensionEligibilityReason(StrEnum):
    ELIGIBLE_BY_SEVERITY = "eligible_by_severity"
    ELIGIBLE_BY_REPETITION = "eligible_by_repetition"
    ELIGIBLE_BY_LOW_COOPERATION = "eligible_by_low_cooperation"
    INELIGIBLE_ISOLATED_LOW_SEVERITY = "ineligible_isolated_low_severity"


@dataclass(frozen=True, kw_only=True)
class ExtensionContext:
    """
    EXT-1/EXT-8: the ONLY inputs should_extend() and its constituent
    functions ever see. Never TrustDomainState, a raw Trust score,
    confidence, trend, GoalEvidence, or a Mandatory Hygiene record.
    """
    intrinsic_severity: SeverityTier
    cooperation: CooperationAssessment
    same_rule_confirmed_incident_count_in_current_window: int
    remaining_active_hour_capacity: float
    occurred_during_recovery_task: bool = False  # always False in this slice -- Recovery Plan does not exist yet (EXT-10)


@dataclass(frozen=True, kw_only=True)
class ExtensionDecision:
    """Append-only (7). Never a bare bool -- every field needed to
    reconstruct why, and by how much, without re-deriving it later."""
    id: str = field(default_factory=new_id)
    created_at: datetime
    incident_id: str
    penalty_window_id: str

    eligible: bool
    eligibility_reason: ExtensionEligibilityReason

    base_hours: float | None
    mitigation_hours: float
    uncapped_hours: float | None
    assigned_hours: float
    capacity_limited: bool  # EXT-6: separate from `eligible`, never conflated

    explanation: str  # EXT-7: always required, non-empty


# -----------------------------------------------------------------------
# Stage 1 — Eligibility (3.1). TBD in the architecture document; this
# slice's own default.
# -----------------------------------------------------------------------

# This slice's own default: "high cooperation" requires BOTH factors,
# not either alone -- a deliberately strict bar, since it is what
# exempts an isolated MINOR/MODERATE Incident from Extension entirely.
# This is the same kind of undecided-ownership policy choice as the
# bootstrap-default-tagged constants below, but has no single constant
# to attach a tag to (it's the AND itself, not a number) -- noted here
# in prose instead.
def _is_high_cooperation(cooperation: CooperationAssessment) -> bool:
    return cooperation.self_disclosed and cooperation.active_cooperation_in_resolution


def determine_extension_eligibility(context: ExtensionContext) -> tuple[bool, ExtensionEligibilityReason]:
    """
    3.1, EXT-3, EXT-4: a deterministic decision table, called exactly
    once per Incident; never revisited by any later stage.
    """
    if context.intrinsic_severity in (SeverityTier.MAJOR, SeverityTier.CRITICAL):
        return True, ExtensionEligibilityReason.ELIGIBLE_BY_SEVERITY

    if context.same_rule_confirmed_incident_count_in_current_window > 1:
        return True, ExtensionEligibilityReason.ELIGIBLE_BY_REPETITION

    if not _is_high_cooperation(context.cooperation):
        return True, ExtensionEligibilityReason.ELIGIBLE_BY_LOW_COOPERATION

    return False, ExtensionEligibilityReason.INELIGIBLE_ISOLATED_LOW_SEVERITY


# -----------------------------------------------------------------------
# Stage 2 — Base Magnitude (3.2). TBD in the architecture document;
# this slice's own defaults.
# -----------------------------------------------------------------------

# BOOTSTRAP_DEFAULT(owner=undecided, mechanism=code):
# Temporary executable value pending an explicit ownership decision.
# Directly determines how many hours an Incident of a given severity
# adds to a Penalty Window -- a strong personal-policy candidate
# (plausibly user-owned), same category as
# penalty_engine/window.py's DEFAULT_BASE_DURATION_HOURS.
BASE_HOURS_BY_SEVERITY: dict[SeverityTier, float] = {
    SeverityTier.MINOR: 4.0,
    SeverityTier.MODERATE: 12.0,
    SeverityTier.MAJOR: 24.0,
    SeverityTier.CRITICAL: 48.0,
}

# BOOTSTRAP_DEFAULT(owner=undecided, mechanism=code):
# Temporary executable value pending an explicit ownership decision.
# Same category as BASE_HOURS_BY_SEVERITY above -- how much repeated
# violations of the same rule add per occurrence.
REPETITION_INCREMENT_HOURS = 6.0


def calculate_base_magnitude(severity: SeverityTier, repetition_count_in_window: int) -> float:
    """3.2: deterministic, table-driven. repetition_count_in_window is
    the SAME current-window-scoped count used in eligibility -- reused,
    not recomputed differently."""
    base = BASE_HOURS_BY_SEVERITY[severity]
    if repetition_count_in_window > 1:
        base += REPETITION_INCREMENT_HOURS * (repetition_count_in_window - 1)
    return base


# -----------------------------------------------------------------------
# Stage 3 — Mitigation (3.3). TBD in the architecture document; this
# slice's own defaults. The architecture document's own illustrative
# comment values (0.5/0.7) are adopted here as this slice's actual
# defaults, since they were explicitly offered as reasonable examples,
# not arbitrary placeholders -- still flagged, not silently presented as
# if the document had already committed to them.
# -----------------------------------------------------------------------

# BOOTSTRAP_DEFAULT(owner=undecided, mechanism=code):
# Temporary executable value pending an explicit ownership decision.
# Unlike BASE_HOURS_BY_SEVERITY/REPETITION_INCREMENT_HOURS above, this
# one is genuinely ambiguous between personal policy and system safety
# policy: EXT-5's floor exists specifically to protect against a
# MAJOR/CRITICAL Incident's consequence being mathematically erased by
# cooperation/context, which is arguably a safety guarantee the system
# itself should own (not something an individual user's preference
# should be able to weaken below), rather than a personal-policy value
# like the base magnitudes above. Marked owner=undecided rather than
# assumed user-owned, specifically because of this ambiguity.
MINIMUM_RETAINED_FRACTION: dict[SeverityTier, float] = {
    SeverityTier.MAJOR: 0.5,
    SeverityTier.CRITICAL: 0.7,
    # MINOR/MODERATE deliberately absent -- EXT-5's floor exists
    # specifically to protect MAJOR/CRITICAL from being mathematically
    # erased by cooperation/context; it is not a general guarantee.
}

# BOOTSTRAP_DEFAULT(owner=undecided, mechanism=code):
# Temporary executable value pending an explicit ownership decision.
# This slice's own graduated mitigation: each factor independently
# softens magnitude by a fixed amount, summed and capped at 1.0 (a
# reduction of 1.0 would erase the base entirely, which the
# MINIMUM_RETAINED_FRACTION floor above still catches for MAJOR/CRITICAL
# regardless of how large this sum gets). Same personal-policy-vs-
# safety-policy ambiguity as MINIMUM_RETAINED_FRACTION above, since
# these three numbers directly determine how much cooperation is
# "worth" against the floor that constant sets.
_SELF_DISCLOSED_MITIGATION = 0.3
_ACTIVE_COOPERATION_MITIGATION = 0.3
_RECOVERY_TASK_MITIGATION = 0.2


def _mitigation_fraction(cooperation: CooperationAssessment, occurred_during_recovery_task: bool) -> float:
    fraction = 0.0
    if cooperation.self_disclosed:
        fraction += _SELF_DISCLOSED_MITIGATION
    if cooperation.active_cooperation_in_resolution:
        fraction += _ACTIVE_COOPERATION_MITIGATION
    if occurred_during_recovery_task:
        fraction += _RECOVERY_TASK_MITIGATION
    return min(fraction, 1.0)


def apply_mitigation(
    base_hours: float, severity: SeverityTier, cooperation: CooperationAssessment, occurred_during_recovery_task: bool,
) -> float:
    """3.3, EXT-5: softens magnitude; never erases the Extension
    consequence of a substantively eligible MAJOR/CRITICAL Incident
    while the floor still applies."""
    reduction = _mitigation_fraction(cooperation, occurred_during_recovery_task)
    mitigated = base_hours * (1.0 - reduction)

    floor_fraction = MINIMUM_RETAINED_FRACTION.get(severity)
    if floor_fraction is not None:
        mitigated = max(mitigated, base_hours * floor_fraction)

    return mitigated


# -----------------------------------------------------------------------
# Stage 4 — Capacity Cap (3.4). Given explicitly, not a TBD parameter --
# this function receives an already-computed capacity, never the
# 336-hour constant itself (Domain Interpretation, 2.11).
# -----------------------------------------------------------------------

def apply_capacity_cap(uncapped_hours: float, remaining_active_hour_capacity: float) -> tuple[float, bool]:
    assigned = min(uncapped_hours, remaining_active_hour_capacity)
    capacity_limited = assigned < uncapped_hours
    return assigned, capacity_limited


# -----------------------------------------------------------------------
# Putting it together (3.5)
# -----------------------------------------------------------------------

def should_extend(
    context: ExtensionContext, incident_id: str, penalty_window_id: str, *, now: datetime,
) -> ExtensionDecision:
    """The single entry point. Each stage's output is consumed by the
    next; no stage reaches backward to change an earlier one (EXT-3)."""
    eligible, reason = determine_extension_eligibility(context)

    if not eligible:
        return ExtensionDecision(
            created_at=now, incident_id=incident_id, penalty_window_id=penalty_window_id,
            eligible=False, eligibility_reason=reason,
            base_hours=None, mitigation_hours=0.0, uncapped_hours=None,
            assigned_hours=0.0, capacity_limited=False,
            explanation=(
                f"Not eligible for Extension: {reason.value}. The Incident remains "
                f"consumed by this window (philosophy.md 3.8) but does not extend it."
            ),
        )

    base_hours = calculate_base_magnitude(
        context.intrinsic_severity, context.same_rule_confirmed_incident_count_in_current_window,
    )
    uncapped_hours = apply_mitigation(
        base_hours, context.intrinsic_severity, context.cooperation, context.occurred_during_recovery_task,
    )
    assigned_hours, capacity_limited = apply_capacity_cap(uncapped_hours, context.remaining_active_hour_capacity)

    return ExtensionDecision(
        created_at=now, incident_id=incident_id, penalty_window_id=penalty_window_id,
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

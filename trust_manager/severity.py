"""
trust_manager/severity.py

The deterministic severity/cooperation rubric.
Canonical: docs/architecture/trust_manager_technical_design.md Sections
5.2, 5.3.

TI20 (`philosophy.md` 2.14/2.18) names these a `critical_change`
category: no runtime or LLM code path exists to tune them
independently -- there simply is no function in this module that
accepts a replacement value for any of them. That much is true today.
What TI20 does NOT yet have is an actual `ConsentRecord`-gated change
mechanism wired to these specific values -- changing any of them today
still means editing this file and shipping a new version, exactly like
changing any other constant. See the `bootstrap-default` tags below (grep for the tag pattern):
several of these numbers were never given a specific value by the
architecture document, and their long-term owner and change mechanism
are genuinely undecided, not merely unimplemented.
"""

from __future__ import annotations

from trust_manager.models import (
    BreachDirectness,
    CooperationAssessment,
    ImpactLevel,
    IncidentEvidence,
    IntentAssessment,
    RepetitionEvidence,
    SeverityTier,
)

__all__ = ["assess_severity", "cooperation_trust_offset", "severity_base_weight"]

# -----------------------------------------------------------------------
# 5.2 — assess_severity() weight table. The POINT VALUES below (0/1/2
# per factor) are the architecture document's own rubric (5.2), not
# bootstrap defaults -- only the final severity_base_weight mapping
# further down (5.3, which converts a SeverityTier into a signed Trust
# impact) lacks an architecture-given number.
# -----------------------------------------------------------------------

_IMPACT_POINTS: dict[ImpactLevel, int] = {
    ImpactLevel.LOW: 0,
    ImpactLevel.MEDIUM: 1,
    ImpactLevel.HIGH: 2,
}

_INTENTIONALITY_POINTS: dict[IntentAssessment, int] = {
    IntentAssessment.UNINTENTIONAL: 0,
    IntentAssessment.UNCLEAR: 0,
    IntentAssessment.DELIBERATE: 1,
}

_BREACH_DIRECTNESS_POINTS: dict[BreachDirectness, int] = {
    BreachDirectness.INDIRECT: 0,
    BreachDirectness.PARTIAL: 1,
    BreachDirectness.DIRECT: 2,
}

_SEVERITY_TIERS = [
    SeverityTier.MINOR,
    SeverityTier.MODERATE,
    SeverityTier.MAJOR,
    SeverityTier.CRITICAL,
]


def _repetition_contribution(rep: RepetitionEvidence) -> int:
    """same_rule_confirmed_count counts ONLY CONFIRMED Incidents (TI17) --
    enforced by the caller providing a correctly-computed RepetitionEvidence,
    not re-checked here (this function trusts its typed input, per TI5's
    same discipline: assess_severity() and its helpers never re-derive
    confirmation state themselves)."""
    if rep.same_rule_confirmed_count <= 1:
        return 0
    if rep.same_rule_confirmed_count <= 3:
        return 1
    return 2


def assess_severity(evidence: IncidentEvidence) -> SeverityTier:
    """
    Called ONLY for Incidents with confirmation=CONFIRMED (5.1, TI15) --
    enforced by the caller (trust_manager/repository.py's confirm_incident()),
    not by this function itself, which has no way to check confirmation
    state (it does not receive an Incident, only its IncidentEvidence).

    MUST NOT accept trust_score, TrustDomainState, or CooperationAssessment
    (TI5) -- the signature is the enforcement mechanism: there is no
    parameter here for either. Two Incidents with identical impact and
    circumstances always produce the same intrinsic_severity, whether
    self-reported or discovered.
    """
    score = 0
    score += _IMPACT_POINTS[evidence.actual_or_potential_impact]
    score += _INTENTIONALITY_POINTS[evidence.intentionality]
    score += _BREACH_DIRECTNESS_POINTS[evidence.rule_breach_directness]
    score += _repetition_contribution(evidence.repetition)

    return _SEVERITY_TIERS[min(score // 2, len(_SEVERITY_TIERS) - 1)]


# -----------------------------------------------------------------------
# 5.3 — cooperation_trust_offset() (a positive factor OUTSIDE intrinsic
# severity, applied only to raw_weight, never to assess_severity()'s input)
# -----------------------------------------------------------------------

# BOOTSTRAP_DEFAULT(owner=undecided, mechanism=code):
# Temporary executable value pending an explicit ownership decision.
# These two offsets directly shape how much self-disclosure/active
# cooperation soften an Incident's impact on Trust -- a personal-policy
# question (how forgiving should the system be toward a specific
# person's cooperative behavior), not a technical or safety parameter.
# Strong candidate for eventual user ownership; not yet decided.
COOPERATION_SELF_DISCLOSURE_OFFSET = 0.02
COOPERATION_ACTIVE_RESOLUTION_OFFSET = 0.02

# BOOTSTRAP_DEFAULT(owner=undecided, mechanism=code):
# Temporary executable value pending an explicit ownership decision.
# The intrinsic-severity-to-raw-weight mapping for TrustEvidence
# (INCIDENT_IMPACT). Always negative -- an Incident's impact on Trust is
# never positive; cooperation only softens it toward zero (see the clamp
# in raw_weight_for_incident() below), it never flips the sign.
# The four specific numbers (-0.05/-0.10/-0.20/-0.35) are this
# implementation's own choice, not given by the architecture document --
# same personal-policy-vs-technical question as the offsets above.
severity_base_weight: dict[SeverityTier, float] = {
    SeverityTier.MINOR: -0.05,
    SeverityTier.MODERATE: -0.10,
    SeverityTier.MAJOR: -0.20,
    SeverityTier.CRITICAL: -0.35,
}

# The floor every raw_weight computed here is clamped to -- guarantees
# cooperation can soften an Incident's impact but can never turn a
# genuinely confirmed Incident into net-positive Trust evidence (5.3's
# "it never flips it to positive for a genuinely serious Incident,"
# generalized to hold for every tier, not only serious ones).
# NOT tagged as a bootstrap default: this is a technical sign-guarantee
# clamp (ensuring the result never crosses zero), not a policy value --
# its magnitude (-0.01) barely matters as long as it stays negative and
# small; unlike the constants above, no plausible owner besides
# "whoever maintains this algorithm" exists for it.
_MIN_INCIDENT_RAW_WEIGHT = -0.01


def cooperation_trust_offset(cooperation: CooperationAssessment) -> float:
    """
    A separate, small POSITIVE adjustment to raw_weight (3.3-equivalent
    in this slice) -- never to intrinsic_severity, which this function
    is never given as an input in the first place (there is no
    signature path for it to affect assess_severity()).
    """
    offset = 0.0
    if cooperation.self_disclosed:
        offset += COOPERATION_SELF_DISCLOSURE_OFFSET
    if cooperation.active_cooperation_in_resolution:
        offset += COOPERATION_ACTIVE_RESOLUTION_OFFSET
    return offset


def raw_weight_for_incident(severity: SeverityTier, cooperation: CooperationAssessment) -> float:
    """
    Computes the raw_weight written into the INCIDENT_IMPACT TrustEvidence
    row created when an Incident reaches CONFIRMED (14.2). Composes the
    two functions above exactly as 5.3 describes: severity sets the base,
    cooperation softens it, never reverses its sign.
    """
    base = severity_base_weight[severity]
    softened = base + cooperation_trust_offset(cooperation)
    return min(softened, _MIN_INCIDENT_RAW_WEIGHT)

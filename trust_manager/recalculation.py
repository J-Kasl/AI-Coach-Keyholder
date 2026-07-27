"""
trust_manager/recalculation.py

Pure, deterministic functions for the score recalculation pipeline.
Canonical: docs/architecture/trust_manager_technical_design.md Sections
2.6, 3.3, 3.5, 3.6. Database access (selecting unconsumed evidence,
writing TrustRecalculation/TrustRecalculationEvidence, updating
TrustDomainState) lives in trust_manager/repository.py — this module
has no database dependency at all, mirroring trust_manager/severity.py.

Every named constant is a `critical_change` parameter (TI20). Two of
them (`MAX_ABS_EFFECTIVE_WEIGHT`, `CONFIDENCE_K`) are NOT given a
specific numeric value anywhere in the architecture document — it says
"capped below a threshold" (3.3/TI9) and "the exact constant k is a
parameter to be tuned" (3.6) without committing to a number. The values
below are this slice's own reasonable defaults, flagged here explicitly
rather than silently presented as if the architecture had already
settled them — see trust_manager/README.md.
"""

from __future__ import annotations

import math

from trust_manager.models import TrustEvidence

__all__ = [
    "effective_weight",
    "apply_recalculation",
    "compute_confidence",
]

# 3.5 — Score Transition Invariants (TI19: |delta| never exceeds this,
# new_score always clamped to [0.0, 1.0])
MAX_ABSOLUTE_DELTA_PER_RECALCULATION = 0.15

# 3.3/TI9 — effective_weight = raw_weight * evidence_confidence, "capped
# below a threshold." The architecture document does not commit to a
# specific number; this is this slice's own default, flagged as such.
MAX_ABS_EFFECTIVE_WEIGHT = 0.5

# 3.6 — the rolling window confidence is computed over.
CONFIDENCE_ROLLING_WINDOW_DAYS = 180

# 3.6 — "the exact constant k is a parameter to be tuned." This slice's
# own default: with k=0.3, a single piece of evidence yields confidence
# ~0.26; confidence approaches 1.0 as evidence accumulates, with
# diminishing returns, as the architecture document specifies the shape
# of (not the exact constant for).
CONFIDENCE_K = 0.3


def effective_weight(evidence: TrustEvidence) -> float:
    """
    3.3/TI9: raw_weight * evidence_confidence, capped so no single piece
    of evidence — however severe or however confidently reported — can
    by itself account for more than MAX_ABS_EFFECTIVE_WEIGHT of a
    recalculation's score delta. (TI19's per-recalculation cap is a
    second, independent safeguard on top of this one — this cap bounds
    one row's contribution; TI19 bounds the recalculation's total.)
    """
    raw = evidence.raw_weight * evidence.evidence_confidence
    return max(-MAX_ABS_EFFECTIVE_WEIGHT, min(MAX_ABS_EFFECTIVE_WEIGHT, raw))


def apply_recalculation(previous_score: float, proposed_delta: float) -> float:
    """
    3.5, TI19. `new_score` is always in [0.0, 1.0] (clamped).
    `|new_score - previous_score| <= MAX_ABSOLUTE_DELTA_PER_RECALCULATION`
    always holds, regardless of how large the sum of effective_weight of
    the input evidence is — this is what makes a single Incident, however
    CRITICAL, unable to destroy a domain's Trust in one recalculation
    (3.5's direct, no-extra-logic-needed consequence).
    """
    bounded_delta = max(
        -MAX_ABSOLUTE_DELTA_PER_RECALCULATION,
        min(MAX_ABSOLUTE_DELTA_PER_RECALCULATION, proposed_delta),
    )
    return max(0.0, min(1.0, previous_score + bounded_delta))


def compute_confidence(applied_evidence_in_window: list[TrustEvidence]) -> float:
    """
    3.6: confidence is not an independently stored number that "just
    is" -- it is derived from the volume of evidence within the rolling
    window, with diminishing returns (more evidence always helps, but by
    a shrinking increment). Deliberately independent of `score` itself
    (this function never receives a score) -- low confidence means the
    score's authority is limited, not that the score is presumed low.

    An empty list (no evidence in the window at all) yields confidence
    0.0 -- the domain's Trust estimate has no support at all until
    evidence accumulates, consistent with the low DEFAULT_NEW_DOMAIN_CONFIDENCE
    a freshly created domain starts with.
    """
    n = len(applied_evidence_in_window)
    return 1.0 - math.exp(-CONFIDENCE_K * n)

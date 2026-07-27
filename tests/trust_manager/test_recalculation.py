"""
tests/trust_manager/test_recalculation.py

Pure-function tests for trust_manager/recalculation.py (3.3, 3.5, 3.6).
No database involved -- these are deterministic computations only.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from trust_manager.models import EvidenceType, TrustEvidence
from trust_manager.recalculation import (
    MAX_ABS_EFFECTIVE_WEIGHT,
    MAX_ABSOLUTE_DELTA_PER_RECALCULATION,
    apply_recalculation,
    compute_confidence,
    effective_weight,
)

FIXED_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _evidence(raw_weight: float, confidence: float) -> TrustEvidence:
    return TrustEvidence(
        domain_id="chastity", created_at=FIXED_TIME, evidence_type=EvidenceType.INCIDENT_IMPACT,
        source_entity_type="incident", source_entity_id="inc-1",
        raw_weight=raw_weight, evidence_confidence=confidence, explanation="test",
    )


class TestEffectiveWeight:
    def test_multiplies_raw_weight_by_confidence(self) -> None:
        assert effective_weight(_evidence(-0.1, 0.5)) == -0.05

    def test_caps_at_max_abs_effective_weight_negative(self) -> None:
        result = effective_weight(_evidence(-10.0, 1.0))
        assert result == -MAX_ABS_EFFECTIVE_WEIGHT

    def test_caps_at_max_abs_effective_weight_positive(self) -> None:
        result = effective_weight(_evidence(10.0, 1.0))
        assert result == MAX_ABS_EFFECTIVE_WEIGHT

    def test_small_weight_uncapped(self) -> None:
        assert effective_weight(_evidence(-0.02, 0.9)) == pytest.approx(-0.018)


class TestApplyRecalculation:
    def test_delta_within_cap_applies_fully(self) -> None:
        assert apply_recalculation(0.6, -0.1) == pytest.approx(0.5)

    def test_delta_beyond_cap_is_bounded(self) -> None:
        """TI19: a single recalculation, however large the proposed
        delta, never moves the score by more than the cap -- a CRITICAL
        Incident cannot destroy a domain in one step."""
        result = apply_recalculation(0.6, -10.0)
        assert result == pytest.approx(0.6 - MAX_ABSOLUTE_DELTA_PER_RECALCULATION)

    def test_positive_delta_also_bounded(self) -> None:
        result = apply_recalculation(0.3, 10.0)
        assert result == pytest.approx(0.3 + MAX_ABSOLUTE_DELTA_PER_RECALCULATION)

    def test_new_score_never_exceeds_one(self) -> None:
        result = apply_recalculation(0.95, 0.15)
        assert result <= 1.0

    def test_new_score_never_below_zero(self) -> None:
        result = apply_recalculation(0.05, -0.15)
        assert result >= 0.0

    def test_zero_delta_is_a_no_op(self) -> None:
        assert apply_recalculation(0.6, 0.0) == pytest.approx(0.6)


class TestComputeConfidence:
    def test_no_evidence_yields_zero_confidence(self) -> None:
        assert compute_confidence([]) == 0.0

    def test_more_evidence_yields_higher_confidence(self) -> None:
        few = [_evidence(-0.05, 0.5)]
        many = [_evidence(-0.05, 0.5) for _ in range(20)]
        assert compute_confidence(many) > compute_confidence(few)

    def test_confidence_is_bounded_below_one(self) -> None:
        """Mathematically approaches but never reaches 1.0 -- checked at
        a moderate n where float64 still shows the gap (an extremely
        large n underflows exp() to exactly 0.0, making 1.0 - 0.0 == 1.0
        a float-precision artifact, not evidence the bound was violated)."""
        moderate = [_evidence(-0.05, 0.5) for _ in range(20)]
        assert compute_confidence(moderate) < 1.0

    def test_confidence_is_independent_of_score(self) -> None:
        """3.6: compute_confidence() never receives a score -- this test
        documents that fact structurally, by confirming the function's
        only input is the evidence list (a TypeError would occur if a
        score parameter were required)."""
        import inspect
        params = inspect.signature(compute_confidence).parameters
        assert list(params.keys()) == ["applied_evidence_in_window"]

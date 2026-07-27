"""
tests/penalty_engine/test_extension.py

Pure-function tests for penalty_engine/extension.py, following the
architecture document's own test matrix (extension_technical_design.md
Section 8, ET1-ET16).
"""

from __future__ import annotations

from datetime import datetime, timezone

from penalty_engine.extension import (
    ExtensionContext,
    ExtensionEligibilityReason,
    apply_capacity_cap,
    apply_mitigation,
    calculate_base_magnitude,
    determine_extension_eligibility,
    should_extend,
)
from trust_manager.models import CooperationAssessment, SeverityTier

FIXED_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)

HIGH_COOPERATION = CooperationAssessment(self_disclosed=True, active_cooperation_in_resolution=True)
LOW_COOPERATION = CooperationAssessment(self_disclosed=False, active_cooperation_in_resolution=False)


def _context(
    severity: SeverityTier = SeverityTier.MINOR,
    cooperation: CooperationAssessment = HIGH_COOPERATION,
    count: int = 1,
    capacity: float = 1000.0,
    recovery_task: bool = False,
) -> ExtensionContext:
    return ExtensionContext(
        intrinsic_severity=severity, cooperation=cooperation,
        same_rule_confirmed_incident_count_in_current_window=count,
        remaining_active_hour_capacity=capacity, occurred_during_recovery_task=recovery_task,
    )


class TestEligibility:
    def test_et1_major_is_always_eligible(self) -> None:
        eligible, reason = determine_extension_eligibility(_context(severity=SeverityTier.MAJOR, count=1, cooperation=HIGH_COOPERATION))
        assert eligible is True
        assert reason == ExtensionEligibilityReason.ELIGIBLE_BY_SEVERITY

    def test_et2_critical_is_always_eligible(self) -> None:
        eligible, reason = determine_extension_eligibility(_context(severity=SeverityTier.CRITICAL, count=1, cooperation=HIGH_COOPERATION))
        assert eligible is True
        assert reason == ExtensionEligibilityReason.ELIGIBLE_BY_SEVERITY

    def test_et3_isolated_minor_high_cooperation_is_ineligible(self) -> None:
        eligible, reason = determine_extension_eligibility(_context(severity=SeverityTier.MINOR, count=1, cooperation=HIGH_COOPERATION))
        assert eligible is False
        assert reason == ExtensionEligibilityReason.INELIGIBLE_ISOLATED_LOW_SEVERITY

    def test_et4_isolated_minor_low_cooperation_is_eligible(self) -> None:
        eligible, reason = determine_extension_eligibility(_context(severity=SeverityTier.MINOR, count=1, cooperation=LOW_COOPERATION))
        assert eligible is True
        assert reason == ExtensionEligibilityReason.ELIGIBLE_BY_LOW_COOPERATION

    def test_et5_repeated_minor_is_eligible_even_with_high_cooperation(self) -> None:
        eligible, reason = determine_extension_eligibility(_context(severity=SeverityTier.MINOR, count=2, cooperation=HIGH_COOPERATION))
        assert eligible is True
        assert reason == ExtensionEligibilityReason.ELIGIBLE_BY_REPETITION

    def test_et6_eligibility_never_revisited_by_downstream_stages(self) -> None:
        context = _context(severity=SeverityTier.CRITICAL, capacity=0.0)  # zero capacity, extreme input
        decision = should_extend(context, "inc-1", "win-1", now=FIXED_TIME)
        assert decision.eligible is True  # capacity=0 must not flip eligibility (EXT-3, EXT-6)


class TestMitigation:
    def test_et7_mitigation_floor_holds_for_major(self) -> None:
        base = 24.0
        result = apply_mitigation(base, SeverityTier.MAJOR, HIGH_COOPERATION, occurred_during_recovery_task=True)
        assert result >= base * 0.5  # MINIMUM_RETAINED_FRACTION[MAJOR]

    def test_et8_mitigation_floor_holds_for_critical(self) -> None:
        base = 48.0
        result = apply_mitigation(base, SeverityTier.CRITICAL, HIGH_COOPERATION, occurred_during_recovery_task=True)
        assert result >= base * 0.7  # MINIMUM_RETAINED_FRACTION[CRITICAL]

    def test_et9_minor_moderate_have_no_floor(self) -> None:
        base = 4.0
        result = apply_mitigation(base, SeverityTier.MINOR, HIGH_COOPERATION, occurred_during_recovery_task=True)
        assert 0.0 <= result < base  # may approach but never needs a floor entry


class TestCapacityCap:
    def test_et10_capacity_cap_is_distinct_from_eligibility(self) -> None:
        context = _context(severity=SeverityTier.CRITICAL, capacity=0.0)
        decision = should_extend(context, "inc-1", "win-1", now=FIXED_TIME)
        assert decision.eligible is True
        assert decision.assigned_hours == 0.0
        assert decision.capacity_limited is True

    def test_capacity_cap_function_directly(self) -> None:
        assigned, limited = apply_capacity_cap(uncapped_hours=20.0, remaining_active_hour_capacity=5.0)
        assert assigned == 5.0
        assert limited is True

    def test_capacity_cap_not_limited_when_capacity_is_sufficient(self) -> None:
        assigned, limited = apply_capacity_cap(uncapped_hours=20.0, remaining_active_hour_capacity=100.0)
        assert assigned == 20.0
        assert limited is False


class TestRepetitionScoping:
    def test_et11_repetition_count_is_current_window_scoped_by_contract(self) -> None:
        """This function trusts its typed input entirely -- the actual
        current-window scoping is enforced by the CALLER
        (penalty_engine/repository.py, which counts only rows in
        incident_consumption for the CURRENT window). This test
        documents the contract: a higher count changes the outcome,
        proving the parameter is load-bearing, not ignored."""
        isolated = _context(severity=SeverityTier.MINOR, count=1, cooperation=HIGH_COOPERATION)
        repeated = _context(severity=SeverityTier.MINOR, count=2, cooperation=HIGH_COOPERATION)
        assert determine_extension_eligibility(isolated)[0] != determine_extension_eligibility(repeated)[0]


class TestExplanation:
    def test_et12_explanation_always_populated_when_ineligible(self) -> None:
        decision = should_extend(_context(severity=SeverityTier.MINOR, count=1, cooperation=HIGH_COOPERATION), "i", "w", now=FIXED_TIME)
        assert decision.explanation.strip() != ""

    def test_et12_explanation_always_populated_when_eligible(self) -> None:
        decision = should_extend(_context(severity=SeverityTier.CRITICAL), "i", "w", now=FIXED_TIME)
        assert decision.explanation.strip() != ""


class TestNoForeignStateRead:
    def test_et13_context_carries_only_permitted_fields(self) -> None:
        """EXT-1/EXT-8: structurally verified -- ExtensionContext simply
        has no field for TrustDomainState/GoalEvidence/etc. to occupy."""
        import dataclasses
        field_names = {f.name for f in dataclasses.fields(ExtensionContext)}
        assert field_names == {
            "intrinsic_severity", "cooperation", "same_rule_confirmed_incident_count_in_current_window",
            "remaining_active_hour_capacity", "occurred_during_recovery_task",
        }


class TestRecoveryTaskContext:
    def test_et16_recovery_task_context_cannot_change_eligibility(self) -> None:
        without = _context(severity=SeverityTier.MAJOR, recovery_task=False)
        with_task = _context(severity=SeverityTier.MAJOR, recovery_task=True)
        eligible_a, reason_a = determine_extension_eligibility(without)
        eligible_b, reason_b = determine_extension_eligibility(with_task)
        assert (eligible_a, reason_a) == (eligible_b, reason_b)

    def test_et16_recovery_task_context_can_change_magnitude(self) -> None:
        base = 24.0
        without = apply_mitigation(base, SeverityTier.MINOR, LOW_COOPERATION, occurred_during_recovery_task=False)
        with_task = apply_mitigation(base, SeverityTier.MINOR, LOW_COOPERATION, occurred_during_recovery_task=True)
        assert with_task < without


class TestBaseMagnitude:
    def test_repetition_increases_base(self) -> None:
        isolated = calculate_base_magnitude(SeverityTier.MINOR, repetition_count_in_window=1)
        repeated = calculate_base_magnitude(SeverityTier.MINOR, repetition_count_in_window=3)
        assert repeated > isolated

    def test_severity_ordering_preserved_in_base_hours(self) -> None:
        minor = calculate_base_magnitude(SeverityTier.MINOR, 1)
        moderate = calculate_base_magnitude(SeverityTier.MODERATE, 1)
        major = calculate_base_magnitude(SeverityTier.MAJOR, 1)
        critical = calculate_base_magnitude(SeverityTier.CRITICAL, 1)
        assert minor < moderate < major < critical

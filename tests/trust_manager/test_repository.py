"""
tests/trust_manager/test_repository.py

Tests for Trust Manager Slice 1 (docs/architecture/trust_manager_technical_design.md
Sections 2.1, 2.2, 2.8, 2.10, 5.1-5.4, 13, 14). See trust_manager/README.md
for exactly what this slice covers.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from infrastructure.database import Database as CoreDatabase
from trust_manager.models import (
    BreachDirectness,
    ConfirmationSource,
    CooperationAssessment,
    EvidenceConfidenceLevel,
    ImpactLevel,
    IncidentConfirmation,
    IncidentEvidence,
    IntentAssessment,
    RepetitionEvidence,
    SeverityTier,
)
from trust_manager.repository import (
    DEFAULT_NEW_DOMAIN_CONFIDENCE,
    DEFAULT_NEW_DOMAIN_SCORE,
    IncidentNotFoundError,
    TrustManager,
)
from trust_manager.severity import assess_severity, cooperation_trust_offset, raw_weight_for_incident

FIXED_TIME = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _apply_migrations(core: CoreDatabase) -> None:
    migrations_dir = Path(__file__).parent.parent.parent / "database" / "migrations"
    with core.raw_connection() as conn:
        for path in sorted(migrations_dir.glob("*.sql")):
            conn.executescript(path.read_text(encoding="utf-8"))


@pytest.fixture
def tm(tmp_path: Path) -> TrustManager:
    core = CoreDatabase(tmp_path / "test.db")
    _apply_migrations(core)
    manager = TrustManager(tmp_path / "test.db", core=core)
    # A domain must exist before any Incident can legitimately reference it
    # (a real FOREIGN KEY, not an oversight) -- created here so individual
    # tests don't have to repeat this setup unless they specifically want
    # to test domain creation/absence itself.
    manager.create_domain(
        domain_id="chastity", display_name="Chastity", description="...",
        created_via_consent_id="setup-consent", now=FIXED_TIME,
    )
    return manager


def _basic_evidence(
    impact: ImpactLevel = ImpactLevel.LOW,
    intent: IntentAssessment = IntentAssessment.UNCLEAR,
    breach: BreachDirectness = BreachDirectness.INDIRECT,
    confidence: EvidenceConfidenceLevel = EvidenceConfidenceLevel.HIGH,
    repetition_count: int = 0,
) -> IncidentEvidence:
    return IncidentEvidence(
        actual_or_potential_impact=impact,
        intentionality=intent,
        rule_breach_directness=breach,
        evidence_confidence=confidence,
        repetition=RepetitionEvidence(same_rule_confirmed_count=repetition_count, evaluation_window_days=30),
    )


class TestDomainRegistry:
    def test_create_domain_initializes_default_state(self, tm: TrustManager) -> None:
        tm.create_domain(
            domain_id="test_domain", display_name="Chastity", description="...",
            created_via_consent_id="consent-1", now=FIXED_TIME,
        )
        state = tm.get_domain_state("test_domain")
        assert state is not None
        assert state.score == DEFAULT_NEW_DOMAIN_SCORE
        assert state.confidence == DEFAULT_NEW_DOMAIN_CONFIDENCE
        assert state.trend == "stable"

    def test_create_domain_with_overrides(self, tm: TrustManager) -> None:
        tm.create_domain(
            domain_id="test_domain", display_name="Chastity", description="...",
            created_via_consent_id="consent-1", now=FIXED_TIME,
            initial_score_override=0.8, initial_confidence_override=0.5,
        )
        state = tm.get_domain_state("test_domain")
        assert state.score == 0.8
        assert state.confidence == 0.5

    def test_create_domain_emits_event(self, tm: TrustManager) -> None:
        tm.create_domain(
            domain_id="test_domain", display_name="Chastity", description="...",
            created_via_consent_id="consent-1", now=FIXED_TIME,
        )
        with tm._core.transaction() as tx:
            row = tx.fetch_one("SELECT * FROM domain_events WHERE event_type = 'trust_domain.created'")
        assert row is not None

    def test_deactivate_and_reactivate_domain(self, tm: TrustManager) -> None:
        tm.create_domain(
            domain_id="test_domain", display_name="Chastity", description="...",
            created_via_consent_id="consent-1", now=FIXED_TIME,
        )
        tm.deactivate_domain("test_domain", via_consent_id="consent-2", now=FIXED_TIME)
        with tm._core.transaction() as tx:
            row = tx.fetch_one("SELECT is_active FROM trust_domains WHERE domain_id = ?", ("test_domain",))
        assert row["is_active"] == 0

        tm.reactivate_domain("test_domain", via_consent_id="consent-3", now=FIXED_TIME)
        with tm._core.transaction() as tx:
            row = tx.fetch_one("SELECT is_active FROM trust_domains WHERE domain_id = ?", ("test_domain",))
        assert row["is_active"] == 1

    def test_get_domain_state_missing_domain_returns_none(self, tm: TrustManager) -> None:
        assert tm.get_domain_state("does-not-exist") is None


class TestIncidentRegistrationAndConfirmationGating:
    def test_register_incident_starts_unconfirmed_with_no_assessment(self, tm: TrustManager) -> None:
        incident = tm.register_incident_report(
            rule_group_id="rg1", trust_domain="chastity", description="late check-in",
            evidence=_basic_evidence(), now=FIXED_TIME,
        )
        assert incident.confirmation == IncidentConfirmation.UNCONFIRMED
        assert incident.assessment is None

    def test_get_incident_assessment_returns_none_for_unconfirmed(self, tm: TrustManager) -> None:
        """TI15/TT27: assessment does not exist before CONFIRMED."""
        incident = tm.register_incident_report(
            rule_group_id="rg1", trust_domain="chastity", description="late check-in",
            evidence=_basic_evidence(), now=FIXED_TIME,
        )
        assert tm.get_incident_assessment(incident.id) is None

    def test_provisional_confirmation_still_has_no_assessment(self, tm: TrustManager) -> None:
        incident = tm.register_incident_report(
            rule_group_id="rg1", trust_domain="chastity", description="late check-in",
            evidence=_basic_evidence(), now=FIXED_TIME,
        )
        tm.confirm_incident(
            incident.id, new_confirmation=IncidentConfirmation.PROVISIONAL,
            source=ConfirmationSource.SYSTEM_VERIFIED, evidence_description="initial system flag",
            now=FIXED_TIME,
        )
        assert tm.get_incident_assessment(incident.id) is None

    def test_get_incident_assessment_missing_incident_returns_none(self, tm: TrustManager) -> None:
        assert tm.get_incident_assessment("does-not-exist") is None


class TestConfirmationReachesConfirmed:
    def test_confirming_writes_assessment_atomically(self, tm: TrustManager) -> None:
        incident = tm.register_incident_report(
            rule_group_id="rg1", trust_domain="chastity", description="late check-in",
            evidence=_basic_evidence(impact=ImpactLevel.HIGH, breach=BreachDirectness.DIRECT),
            now=FIXED_TIME,
        )
        tm.confirm_incident(
            incident.id, new_confirmation=IncidentConfirmation.CONFIRMED,
            source=ConfirmationSource.USER_ACKNOWLEDGED, evidence_description="user admitted it",
            now=FIXED_TIME,
        )

        assessment = tm.get_incident_assessment(incident.id)
        assert assessment is not None
        assert assessment.intrinsic_severity == SeverityTier.MAJOR  # impact=2 + breach=2 = 4 -> //2=2 -> MAJOR

    def test_confirming_writes_trust_evidence(self, tm: TrustManager) -> None:
        incident = tm.register_incident_report(
            rule_group_id="rg1", trust_domain="chastity", description="late check-in",
            evidence=_basic_evidence(), now=FIXED_TIME,
        )
        tm.confirm_incident(
            incident.id, new_confirmation=IncidentConfirmation.CONFIRMED,
            source=ConfirmationSource.USER_ACKNOWLEDGED, evidence_description="admitted",
            now=FIXED_TIME,
        )
        with tm._core.transaction() as tx:
            row = tx.fetch_one(
                "SELECT * FROM trust_evidence WHERE source_entity_type = 'incident' AND source_entity_id = ?",
                (incident.id,),
            )
        assert row is not None
        assert row["raw_weight"] < 0  # an Incident's impact is never positive Trust evidence

    def test_confirming_emits_both_events(self, tm: TrustManager) -> None:
        incident = tm.register_incident_report(
            rule_group_id="rg1", trust_domain="chastity", description="late check-in",
            evidence=_basic_evidence(), now=FIXED_TIME,
        )
        tm.confirm_incident(
            incident.id, new_confirmation=IncidentConfirmation.CONFIRMED,
            source=ConfirmationSource.USER_ACKNOWLEDGED, evidence_description="admitted",
            now=FIXED_TIME,
        )
        with tm._core.transaction() as tx:
            events = tx.fetch_all("SELECT event_type FROM domain_events ORDER BY created_at")
        event_types = [e["event_type"] for e in events]
        assert "incident.confirmation_changed" in event_types
        assert "trust_evidence.recorded" in event_types

    def test_single_canonical_confirmation_event_carries_transition_in_payload(self, tm: TrustManager) -> None:
        """Finding 1 (domain_events_catalog.md): one event, filtered by payload."""
        import json
        incident = tm.register_incident_report(
            rule_group_id="rg1", trust_domain="chastity", description="late check-in",
            evidence=_basic_evidence(), now=FIXED_TIME,
        )
        tm.confirm_incident(
            incident.id, new_confirmation=IncidentConfirmation.CONFIRMED,
            source=ConfirmationSource.USER_ACKNOWLEDGED, evidence_description="admitted",
            now=FIXED_TIME,
        )
        with tm._core.transaction() as tx:
            row = tx.fetch_one(
                "SELECT payload_json FROM domain_events WHERE event_type = 'incident.confirmation_changed'"
            )
        payload = json.loads(row["payload_json"])
        assert payload["previous_confirmation"] == "unconfirmed"
        assert payload["new_confirmation"] == "confirmed"

    def test_confirm_missing_incident_raises(self, tm: TrustManager) -> None:
        with pytest.raises(IncidentNotFoundError):
            tm.confirm_incident(
                "does-not-exist", new_confirmation=IncidentConfirmation.CONFIRMED,
                source=ConfirmationSource.USER_ACKNOWLEDGED, evidence_description="x",
                now=FIXED_TIME,
            )

    def test_cooperation_softens_raw_weight_but_never_flips_sign(self, tm: TrustManager) -> None:
        incident = tm.register_incident_report(
            rule_group_id="rg1", trust_domain="chastity", description="late check-in",
            evidence=_basic_evidence(impact=ImpactLevel.LOW, breach=BreachDirectness.INDIRECT),
            now=FIXED_TIME,
        )
        tm.confirm_incident(
            incident.id, new_confirmation=IncidentConfirmation.CONFIRMED,
            source=ConfirmationSource.USER_ACKNOWLEDGED, evidence_description="admitted",
            cooperation=CooperationAssessment(self_disclosed=True, active_cooperation_in_resolution=True),
            now=FIXED_TIME,
        )
        with tm._core.transaction() as tx:
            row = tx.fetch_one(
                "SELECT raw_weight FROM trust_evidence WHERE source_entity_id = ?", (incident.id,)
            )
        assert row["raw_weight"] < 0  # never flips positive, even with full cooperation


class TestAssessSeverityRubric:
    """5.2 -- pure function, deterministic, TI5-compliant signature."""

    def test_minor_severity(self) -> None:
        ev = _basic_evidence(impact=ImpactLevel.LOW, intent=IntentAssessment.UNINTENTIONAL, breach=BreachDirectness.INDIRECT)
        assert assess_severity(ev) == SeverityTier.MINOR

    def test_critical_severity(self) -> None:
        ev = _basic_evidence(
            impact=ImpactLevel.HIGH, intent=IntentAssessment.DELIBERATE, breach=BreachDirectness.DIRECT, repetition_count=4,
        )
        assert assess_severity(ev) == SeverityTier.CRITICAL

    def test_repetition_only_counts_beyond_one(self) -> None:
        low_rep = _basic_evidence(repetition_count=1)
        high_rep = _basic_evidence(repetition_count=5)
        assert assess_severity(high_rep) != assess_severity(low_rep)

    def test_identical_evidence_produces_identical_severity_regardless_of_self_report(self) -> None:
        """TI5: intrinsic_severity does not depend on cooperation/confirmation source."""
        ev = _basic_evidence(impact=ImpactLevel.MEDIUM)
        assert assess_severity(ev) == assess_severity(ev)  # deterministic, same input -> same output


class TestCooperationOffset:
    def test_no_cooperation_is_zero(self) -> None:
        assert cooperation_trust_offset(CooperationAssessment()) == 0.0

    def test_both_factors_additive(self) -> None:
        full = CooperationAssessment(self_disclosed=True, active_cooperation_in_resolution=True)
        partial = CooperationAssessment(self_disclosed=True)
        assert cooperation_trust_offset(full) > cooperation_trust_offset(partial) > 0

    def test_raw_weight_for_incident_never_positive(self) -> None:
        full_cooperation = CooperationAssessment(self_disclosed=True, active_cooperation_in_resolution=True)
        assert raw_weight_for_incident(SeverityTier.MINOR, full_cooperation) < 0


class TestPublicReadAPI:
    def test_get_confirmed_incidents_since_excludes_unconfirmed(self, tm: TrustManager) -> None:
        confirmed = tm.register_incident_report(
            rule_group_id="rg1", trust_domain="chastity", description="a",
            evidence=_basic_evidence(), now=FIXED_TIME,
        )
        unconfirmed = tm.register_incident_report(
            rule_group_id="rg2", trust_domain="chastity", description="b",
            evidence=_basic_evidence(), now=FIXED_TIME,
        )
        tm.confirm_incident(
            confirmed.id, new_confirmation=IncidentConfirmation.CONFIRMED,
            source=ConfirmationSource.USER_ACKNOWLEDGED, evidence_description="admitted",
            now=FIXED_TIME,
        )

        summaries = tm.get_confirmed_incidents_since(FIXED_TIME - timedelta(days=1))
        ids = [s.id for s in summaries]
        assert confirmed.id in ids
        assert unconfirmed.id not in ids

    def test_get_confirmed_incidents_since_respects_time_bound(self, tm: TrustManager) -> None:
        incident = tm.register_incident_report(
            rule_group_id="rg1", trust_domain="chastity", description="a",
            evidence=_basic_evidence(), now=FIXED_TIME,
        )
        tm.confirm_incident(
            incident.id, new_confirmation=IncidentConfirmation.CONFIRMED,
            source=ConfirmationSource.USER_ACKNOWLEDGED, evidence_description="admitted",
            now=FIXED_TIME,
        )
        summaries = tm.get_confirmed_incidents_since(FIXED_TIME + timedelta(days=1))
        assert summaries == []

    def test_summary_exposes_only_four_fields(self, tm: TrustManager) -> None:
        """13: deliberately NOT the full Incident/IncidentAssessment.
        rule_group_id added for Extension's EXT-2 repetition count."""
        incident = tm.register_incident_report(
            rule_group_id="rg1", trust_domain="chastity", description="a",
            evidence=_basic_evidence(), now=FIXED_TIME,
        )
        tm.confirm_incident(
            incident.id, new_confirmation=IncidentConfirmation.CONFIRMED,
            source=ConfirmationSource.USER_ACKNOWLEDGED, evidence_description="admitted",
            now=FIXED_TIME,
        )
        summary = tm.get_confirmed_incidents_since(FIXED_TIME - timedelta(days=1))[0]
        field_names = {f.name for f in __import__("dataclasses").fields(summary)}
        assert field_names == {"id", "trust_domain", "rule_group_id", "created_at"}


class TestRecalculationIntegration:
    """3.1-3.6, wired through confirm_incident()'s 'incident' trigger."""

    def test_confirming_incident_updates_domain_score(self, tm: TrustManager) -> None:
        state_before = tm.get_domain_state("chastity")
        incident = tm.register_incident_report(
            rule_group_id="rg1", trust_domain="chastity", description="a",
            evidence=_basic_evidence(impact=ImpactLevel.HIGH, breach=BreachDirectness.DIRECT),
            now=FIXED_TIME,
        )
        tm.confirm_incident(
            incident.id, new_confirmation=IncidentConfirmation.CONFIRMED,
            source=ConfirmationSource.USER_ACKNOWLEDGED, evidence_description="admitted",
            now=FIXED_TIME,
        )
        state_after = tm.get_domain_state("chastity")
        assert state_after.score < state_before.score

    def test_score_never_moves_more_than_the_cap_even_for_critical(self, tm: TrustManager) -> None:
        """TI19: a single CONFIRMED CRITICAL Incident cannot destroy the domain."""
        from trust_manager.recalculation import MAX_ABSOLUTE_DELTA_PER_RECALCULATION

        state_before = tm.get_domain_state("chastity")
        incident = tm.register_incident_report(
            rule_group_id="rg1", trust_domain="chastity", description="a",
            evidence=_basic_evidence(
                impact=ImpactLevel.HIGH, intent=IntentAssessment.DELIBERATE,
                breach=BreachDirectness.DIRECT, repetition_count=4,
            ),
            now=FIXED_TIME,
        )
        tm.confirm_incident(
            incident.id, new_confirmation=IncidentConfirmation.CONFIRMED,
            source=ConfirmationSource.USER_ACKNOWLEDGED, evidence_description="admitted",
            now=FIXED_TIME,
        )
        state_after = tm.get_domain_state("chastity")
        assert (state_before.score - state_after.score) <= MAX_ABSOLUTE_DELTA_PER_RECALCULATION + 1e-9

    def test_evidence_is_consumed_exactly_once(self, tm: TrustManager) -> None:
        """TI4: a second recalculation must not re-consume the same evidence."""
        incident = tm.register_incident_report(
            rule_group_id="rg1", trust_domain="chastity", description="a",
            evidence=_basic_evidence(), now=FIXED_TIME,
        )
        tm.confirm_incident(
            incident.id, new_confirmation=IncidentConfirmation.CONFIRMED,
            source=ConfirmationSource.USER_ACKNOWLEDGED, evidence_description="admitted",
            now=FIXED_TIME,
        )
        state_after_first = tm.get_domain_state("chastity")

        # A second, independent recalculation (as 'scheduled_review'
        # would trigger) with no new evidence must not move the score
        # again from the same, already-consumed evidence.
        tm.recalculate_domain_trust("chastity", triggered_by="scheduled_review", now=FIXED_TIME + timedelta(days=1))
        state_after_second = tm.get_domain_state("chastity")
        assert state_after_second.score == state_after_first.score

    def test_recalculation_with_no_new_evidence_is_confidence_only(self, tm: TrustManager) -> None:
        """TI10b: a purely staleness-driven recalculation writes no new
        TrustRecalculationEvidence rows and leaves score unchanged."""
        result = tm.recalculate_domain_trust("chastity", triggered_by="scheduled_review", now=FIXED_TIME)
        assert result.new_score == result.previous_score

        with tm._core.transaction() as tx:
            count = tx.fetch_one(
                "SELECT COUNT(*) as n FROM trust_recalculation_evidence WHERE recalculation_id = ?",
                (result.id,),
            )
        assert count["n"] == 0

    def test_recalculation_emits_event(self, tm: TrustManager) -> None:
        incident = tm.register_incident_report(
            rule_group_id="rg1", trust_domain="chastity", description="a",
            evidence=_basic_evidence(), now=FIXED_TIME,
        )
        tm.confirm_incident(
            incident.id, new_confirmation=IncidentConfirmation.CONFIRMED,
            source=ConfirmationSource.USER_ACKNOWLEDGED, evidence_description="admitted",
            now=FIXED_TIME,
        )
        with tm._core.transaction() as tx:
            row = tx.fetch_one("SELECT * FROM domain_events WHERE event_type = 'trust_domain.recalculated'")
        assert row is not None

    def test_recalculation_has_non_empty_explanation(self, tm: TrustManager) -> None:
        """TI10."""
        result = tm.recalculate_domain_trust("chastity", triggered_by="scheduled_review", now=FIXED_TIME)
        assert result.explanation.strip() != ""

    def test_repeated_recalculation_with_same_evidence_is_stable(self, tm: TrustManager) -> None:
        """Idempotent in the sense that matters here: running a second
        recalculation with nothing new to consume never re-derives a
        different score from history -- the score is stable once all
        existing evidence has been consumed."""
        incident = tm.register_incident_report(
            rule_group_id="rg1", trust_domain="chastity", description="a",
            evidence=_basic_evidence(), now=FIXED_TIME,
        )
        tm.confirm_incident(
            incident.id, new_confirmation=IncidentConfirmation.CONFIRMED,
            source=ConfirmationSource.USER_ACKNOWLEDGED, evidence_description="admitted",
            now=FIXED_TIME,
        )
        first = tm.get_domain_state("chastity")
        tm.recalculate_domain_trust("chastity", triggered_by="scheduled_review", now=FIXED_TIME + timedelta(days=1))
        second = tm.get_domain_state("chastity")
        tm.recalculate_domain_trust("chastity", triggered_by="scheduled_review", now=FIXED_TIME + timedelta(days=2))
        third = tm.get_domain_state("chastity")
        assert first.score == second.score == third.score


class TestCrashRecovery:
    def test_recover_repairs_confirmed_incident_with_null_assessment(self, tm: TrustManager) -> None:
        """TT26: simulates data from before the TI23 fix by writing
        directly, bypassing confirm_incident()'s atomic path."""
        incident = tm.register_incident_report(
            rule_group_id="rg1", trust_domain="chastity", description="a",
            evidence=_basic_evidence(impact=ImpactLevel.HIGH), now=FIXED_TIME,
        )
        # Simulate the anomaly: confirmation reached CONFIRMED, but the
        # assessment write never happened (as if interrupted mid-way).
        with tm._core.transaction() as tx:
            tx.execute("UPDATE incidents SET confirmation = 'confirmed' WHERE id = ?", (incident.id,))

        assert tm.get_confirmed_incidents_with_null_assessment() != []

        repaired_count = tm.recover_trust_manager_state(FIXED_TIME + timedelta(hours=1))

        assert repaired_count == 1
        assert tm.get_confirmed_incidents_with_null_assessment() == []
        assert tm.get_incident_assessment(incident.id) is not None

    def test_recover_is_idempotent(self, tm: TrustManager) -> None:
        """TI24: repeated runs produce the same result as a single run."""
        incident = tm.register_incident_report(
            rule_group_id="rg1", trust_domain="chastity", description="a",
            evidence=_basic_evidence(), now=FIXED_TIME,
        )
        with tm._core.transaction() as tx:
            tx.execute("UPDATE incidents SET confirmation = 'confirmed' WHERE id = ?", (incident.id,))

        first_run = tm.recover_trust_manager_state(FIXED_TIME)
        second_run = tm.recover_trust_manager_state(FIXED_TIME)

        assert first_run == 1
        assert second_run == 0

    def test_recover_does_not_touch_incidents_with_assessment_already_present(self, tm: TrustManager) -> None:
        incident = tm.register_incident_report(
            rule_group_id="rg1", trust_domain="chastity", description="a",
            evidence=_basic_evidence(), now=FIXED_TIME,
        )
        tm.confirm_incident(
            incident.id, new_confirmation=IncidentConfirmation.CONFIRMED,
            source=ConfirmationSource.USER_ACKNOWLEDGED, evidence_description="admitted",
            now=FIXED_TIME,
        )
        assert tm.recover_trust_manager_state(FIXED_TIME) == 0

    def test_recover_does_not_touch_unconfirmed_incidents(self, tm: TrustManager) -> None:
        tm.register_incident_report(
            rule_group_id="rg1", trust_domain="chastity", description="a",
            evidence=_basic_evidence(), now=FIXED_TIME,
        )
        assert tm.recover_trust_manager_state(FIXED_TIME) == 0

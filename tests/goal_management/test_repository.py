"""
tests/goal_management/test_repository.py

Tests for goal_management/repository.py
(docs/architecture/goal_technical_design.md Sections 2-5, 8-9).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from goal_management.models import (
    GoalChangeProposalNotFoundError,
    GoalInterventionType,
    GoalLifecycleStatus,
    GoalNotFoundError,
    GoalOutcome,
    GoalProposalStatus,
    InvalidGoalTransitionError,
    InvalidProposalStateError,
)
from goal_management.repository import GoalManager
from infrastructure.database import Database as CoreDatabase

FIXED_TIME = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _apply_migrations(core: CoreDatabase) -> None:
    migrations_dir = Path(__file__).parent.parent.parent / "database" / "migrations"
    with core.raw_connection() as conn:
        for path in sorted(migrations_dir.glob("*.sql")):
            conn.executescript(path.read_text(encoding="utf-8"))


@pytest.fixture
def core(tmp_path: Path) -> CoreDatabase:
    c = CoreDatabase(tmp_path / "test.db")
    _apply_migrations(c)
    return c


@pytest.fixture
def gm(core: CoreDatabase) -> GoalManager:
    return GoalManager(core.db_path, core=core)


def _create_goal(gm: GoalManager, now: datetime = FIXED_TIME):
    return gm.create_goal(
        title="Exercise several times per week", target_description="3 workouts per week",
        trust_domain="fitness", created_via="user_proposed", now=now,
    )


class TestGoalStructuralIsolation:
    def test_goal_1_no_trust_manager_or_penalty_engine_import(self) -> None:
        """GOAL-1, enforced structurally: this module never imports
        trust_manager or penalty_engine at all. Checks actual import
        statement lines (not a bare substring match, which would also
        false-positive on this module's own prose describing the rule)."""
        import goal_management.models
        import goal_management.repository
        for module in (goal_management.models, goal_management.repository):
            source = Path(module.__file__).read_text(encoding="utf-8")
            import_lines = [
                line.strip() for line in source.splitlines()
                if line.strip().startswith("import ") or line.strip().startswith("from ")
            ]
            for line in import_lines:
                assert "trust_manager" not in line, f"unexpected import in {module.__name__}: {line!r}"
                assert "penalty_engine" not in line, f"unexpected import in {module.__name__}: {line!r}"


class TestGoalCreation:
    def test_create_goal_starts_active(self, gm: GoalManager) -> None:
        goal = _create_goal(gm)
        assert goal.status == GoalLifecycleStatus.ACTIVE
        assert goal.archived_at is None

    def test_create_goal_writes_version_1(self, gm: GoalManager) -> None:
        goal = _create_goal(gm)
        version = gm.get_goal_version(goal.current_version_id)
        assert version.version == 1
        assert version.adaptation_reason is None
        assert version.supersedes_id is None

    def test_created_event_emitted(self, gm: GoalManager, core: CoreDatabase) -> None:
        _create_goal(gm)
        with core.transaction() as tx:
            row = tx.fetch_one("SELECT * FROM domain_events WHERE event_type = 'goal.created'")
        assert row is not None


class TestDirectLifecycleTransitions:
    def test_pause_then_resume(self, gm: GoalManager) -> None:
        goal = _create_goal(gm)
        gm.pause_goal(goal.goal_group_id, "exam period", now=FIXED_TIME + timedelta(days=1))
        paused = gm.get_goal(goal.goal_group_id)
        assert paused.status == GoalLifecycleStatus.PAUSED

        gm.resume_goal(goal.goal_group_id, now=FIXED_TIME + timedelta(days=2))
        resumed = gm.get_goal(goal.goal_group_id)
        assert resumed.status == GoalLifecycleStatus.ACTIVE

    def test_cannot_pause_an_already_paused_goal(self, gm: GoalManager) -> None:
        goal = _create_goal(gm)
        gm.pause_goal(goal.goal_group_id, "reason", now=FIXED_TIME + timedelta(days=1))
        with pytest.raises(InvalidGoalTransitionError):
            gm.pause_goal(goal.goal_group_id, "reason again", now=FIXED_TIME + timedelta(days=2))

    def test_complete_goal_from_active(self, gm: GoalManager) -> None:
        goal = _create_goal(gm)
        gm.complete_goal(goal.goal_group_id, "durably achieved", now=FIXED_TIME + timedelta(days=30))
        completed = gm.get_goal(goal.goal_group_id)
        assert completed.status == GoalLifecycleStatus.COMPLETED

    def test_complete_goal_from_paused(self, gm: GoalManager) -> None:
        goal = _create_goal(gm)
        gm.pause_goal(goal.goal_group_id, "reason", now=FIXED_TIME + timedelta(days=1))
        gm.complete_goal(goal.goal_group_id, "durably achieved", now=FIXED_TIME + timedelta(days=30))
        completed = gm.get_goal(goal.goal_group_id)
        assert completed.status == GoalLifecycleStatus.COMPLETED

    def test_cannot_complete_an_already_completed_goal(self, gm: GoalManager) -> None:
        goal = _create_goal(gm)
        gm.complete_goal(goal.goal_group_id, "reason", now=FIXED_TIME + timedelta(days=1))
        with pytest.raises(InvalidGoalTransitionError):
            gm.complete_goal(goal.goal_group_id, "reason again", now=FIXED_TIME + timedelta(days=2))

    def test_transition_on_missing_goal_raises(self, gm: GoalManager) -> None:
        with pytest.raises(GoalNotFoundError):
            gm.pause_goal("does-not-exist", "reason", now=FIXED_TIME)


class TestArchiving:
    def test_archive_requires_terminal_status(self, gm: GoalManager) -> None:
        goal = _create_goal(gm)
        with pytest.raises(InvalidGoalTransitionError):
            gm.archive_goal(goal.goal_group_id, now=FIXED_TIME)

    def test_archive_after_completion_succeeds(self, gm: GoalManager) -> None:
        goal = _create_goal(gm)
        gm.complete_goal(goal.goal_group_id, "reason", now=FIXED_TIME + timedelta(days=1))
        gm.archive_goal(goal.goal_group_id, now=FIXED_TIME + timedelta(days=2))
        archived = gm.get_goal(goal.goal_group_id)
        assert archived.archived_at is not None

    def test_archiving_never_changes_status(self, gm: GoalManager) -> None:
        """GOAL-11: archived_at has no behavioral effect -- status stays COMPLETED, not some 'archived' value."""
        goal = _create_goal(gm)
        gm.complete_goal(goal.goal_group_id, "reason", now=FIXED_TIME + timedelta(days=1))
        gm.archive_goal(goal.goal_group_id, now=FIXED_TIME + timedelta(days=2))
        archived = gm.get_goal(goal.goal_group_id)
        assert archived.status == GoalLifecycleStatus.COMPLETED


class TestGoalEvidence:
    def test_record_evidence(self, gm: GoalManager) -> None:
        goal = _create_goal(gm)
        evidence = gm.record_evidence(
            goal.goal_group_id, goal.current_version_id,
            period_start=FIXED_TIME, period_end=FIXED_TIME + timedelta(days=7),
            outcome=GoalOutcome.MET, observed_progress="3 workouts logged", source="check_in",
            now=FIXED_TIME + timedelta(days=7),
        )
        assert evidence.outcome == GoalOutcome.MET

    def test_goal_2_evidence_alone_never_changes_goal_status(self, gm: GoalManager) -> None:
        """GOAL-2: no single GoalEvidence row, of any outcome, automatically
        triggers a lifecycle transition."""
        goal = _create_goal(gm)
        gm.record_evidence(
            goal.goal_group_id, goal.current_version_id,
            period_start=FIXED_TIME, period_end=FIXED_TIME + timedelta(days=7),
            outcome=GoalOutcome.MISSED, observed_progress="0 workouts logged", source="check_in",
            now=FIXED_TIME + timedelta(days=7),
        )
        still_active = gm.get_goal(goal.goal_group_id)
        assert still_active.status == GoalLifecycleStatus.ACTIVE

    def test_evidence_recorded_event_emitted(self, gm: GoalManager, core: CoreDatabase) -> None:
        goal = _create_goal(gm)
        gm.record_evidence(
            goal.goal_group_id, goal.current_version_id,
            period_start=FIXED_TIME, period_end=FIXED_TIME + timedelta(days=7),
            outcome=GoalOutcome.MET, observed_progress="progress", source="check_in",
            now=FIXED_TIME + timedelta(days=7),
        )
        with core.transaction() as tx:
            row = tx.fetch_one("SELECT * FROM domain_events WHERE event_type = 'goal_evidence.recorded'")
        assert row is not None


class TestGoalEvaluation:
    def test_record_evaluation(self, gm: GoalManager) -> None:
        goal = _create_goal(gm)
        evidence = gm.record_evidence(
            goal.goal_group_id, goal.current_version_id,
            period_start=FIXED_TIME, period_end=FIXED_TIME + timedelta(days=7),
            outcome=GoalOutcome.MISSED, observed_progress="0 workouts", source="check_in",
            now=FIXED_TIME + timedelta(days=7),
        )
        evaluation = gm.record_evaluation(
            goal.goal_group_id, triggering_evidence_ids=(evidence.id,),
            findings="Missed due to a busy week; the target itself still seems right.",
            proposed_intervention=GoalInterventionType.NO_CHANGE,
            proposed_intervention_detail="Acknowledge and continue.",
            now=FIXED_TIME + timedelta(days=8),
        )
        assert evaluation.proposed_intervention == GoalInterventionType.NO_CHANGE

    def test_goal_3_empty_triggering_evidence_ids_raises(self, gm: GoalManager) -> None:
        goal = _create_goal(gm)
        with pytest.raises(ValueError):
            gm.record_evaluation(
                goal.goal_group_id, triggering_evidence_ids=(),
                findings="findings", proposed_intervention=GoalInterventionType.NO_CHANGE,
                proposed_intervention_detail="detail", now=FIXED_TIME,
            )

    def test_evaluation_has_no_accountability_field(self) -> None:
        """GOAL-9: GoalEvaluation has no field answering the
        accountability question."""
        import dataclasses
        from goal_management.models import GoalEvaluation
        field_names = {f.name for f in dataclasses.fields(GoalEvaluation)}
        assert "relevant_to_trust" not in field_names
        assert not any("accountab" in name.lower() for name in field_names)


class TestChangeProposalAdaptTarget:
    def test_accepting_adapt_target_creates_new_version(self, gm: GoalManager) -> None:
        goal = _create_goal(gm)
        proposal = gm.create_change_proposal(
            goal.goal_group_id, GoalInterventionType.ADAPT_TARGET, reason="exam period",
            proposal_expires_at=FIXED_TIME + timedelta(days=1),
            proposed_target_description="2 workouts per week", now=FIXED_TIME,
        )
        gm.accept_proposal(proposal.id, now=FIXED_TIME + timedelta(hours=1))

        refreshed = gm.get_goal(goal.goal_group_id)
        new_version = gm.get_goal_version(refreshed.current_version_id)
        assert new_version.version == 2
        assert new_version.target_description == "2 workouts per week"
        assert new_version.adaptation_reason == "exam period"
        assert new_version.supersedes_id == goal.current_version_id

    def test_goal_5_adaptation_reason_required_beyond_version_1(self, gm: GoalManager) -> None:
        """Enforced at the proposal-content level: adapt_target's content.reason
        always becomes the new version's adaptation_reason -- never None
        beyond version 1."""
        goal = _create_goal(gm)
        proposal = gm.create_change_proposal(
            goal.goal_group_id, GoalInterventionType.ADAPT_TARGET, reason="calibration",
            proposal_expires_at=FIXED_TIME + timedelta(days=1),
            proposed_target_description="4 workouts per week", now=FIXED_TIME,
        )
        gm.accept_proposal(proposal.id, now=FIXED_TIME + timedelta(hours=1))
        refreshed = gm.get_goal(goal.goal_group_id)
        new_version = gm.get_goal_version(refreshed.current_version_id)
        assert new_version.adaptation_reason is not None

    def test_goal_6_acceptance_applies_recorded_content_not_current_context(self, gm: GoalManager) -> None:
        goal = _create_goal(gm)
        proposal = gm.create_change_proposal(
            goal.goal_group_id, GoalInterventionType.ADAPT_TARGET, reason="reason",
            proposal_expires_at=FIXED_TIME + timedelta(days=1),
            proposed_title="New title", proposed_target_description="New target", now=FIXED_TIME,
        )
        gm.accept_proposal(proposal.id, now=FIXED_TIME + timedelta(hours=1))
        refreshed = gm.get_goal(goal.goal_group_id)
        new_version = gm.get_goal_version(refreshed.current_version_id)
        assert new_version.title == "New title"
        assert new_version.target_description == "New target"

    def test_adapt_target_only_permitted_from_active(self, gm: GoalManager) -> None:
        goal = _create_goal(gm)
        gm.pause_goal(goal.goal_group_id, "reason", now=FIXED_TIME + timedelta(hours=1))
        proposal = gm.create_change_proposal(
            goal.goal_group_id, GoalInterventionType.ADAPT_TARGET, reason="reason",
            proposal_expires_at=FIXED_TIME + timedelta(days=1),
            proposed_target_description="new target", now=FIXED_TIME + timedelta(hours=2),
        )
        with pytest.raises(InvalidGoalTransitionError):
            gm.accept_proposal(proposal.id, now=FIXED_TIME + timedelta(hours=3))


class TestChangeProposalReplacement:
    def test_accepting_replacement_creates_new_goal_and_marks_original_replaced(self, gm: GoalManager) -> None:
        goal = _create_goal(gm)
        proposal = gm.create_change_proposal(
            goal.goal_group_id, GoalInterventionType.PROPOSE_REPLACEMENT, reason="wrong direction entirely",
            proposal_expires_at=FIXED_TIME + timedelta(days=1),
            proposed_title="Strength training instead", proposed_target_description="2 sessions/week",
            now=FIXED_TIME,
        )
        gm.accept_proposal(proposal.id, now=FIXED_TIME + timedelta(hours=1))

        original = gm.get_goal(goal.goal_group_id)
        assert original.status == GoalLifecycleStatus.REPLACED

    def test_replacement_inherits_trust_domain(self, gm: GoalManager) -> None:
        goal = _create_goal(gm)  # trust_domain="fitness"
        proposal = gm.create_change_proposal(
            goal.goal_group_id, GoalInterventionType.PROPOSE_REPLACEMENT, reason="reason",
            proposal_expires_at=FIXED_TIME + timedelta(days=1),
            proposed_title="New goal", proposed_target_description="New target", now=FIXED_TIME,
        )
        gm.accept_proposal(proposal.id, now=FIXED_TIME + timedelta(hours=1))

        with gm._core.transaction() as tx:
            new_goal_row = tx.fetch_one(
                "SELECT * FROM goals WHERE replaces_goal_group_id = ?", (goal.goal_group_id,),
            )
        new_version = gm.get_goal_version(new_goal_row["current_version_id"])
        assert new_version.trust_domain == "fitness"


class TestChangeProposalAbandonment:
    def test_accepting_abandonment_transitions_goal(self, gm: GoalManager) -> None:
        goal = _create_goal(gm)
        proposal = gm.create_change_proposal(
            goal.goal_group_id, GoalInterventionType.PROPOSE_ABANDONMENT, reason="no longer relevant",
            proposal_expires_at=FIXED_TIME + timedelta(days=1), now=FIXED_TIME,
        )
        gm.accept_proposal(proposal.id, now=FIXED_TIME + timedelta(hours=1))
        abandoned = gm.get_goal(goal.goal_group_id)
        assert abandoned.status == GoalLifecycleStatus.ABANDONED


class TestChangeProposalWorkflow:
    def test_decline_proposal_has_no_effect_on_goal(self, gm: GoalManager) -> None:
        goal = _create_goal(gm)
        proposal = gm.create_change_proposal(
            goal.goal_group_id, GoalInterventionType.PROPOSE_ABANDONMENT, reason="reason",
            proposal_expires_at=FIXED_TIME + timedelta(days=1), now=FIXED_TIME,
        )
        gm.decline_proposal(proposal.id, now=FIXED_TIME + timedelta(hours=1))

        still_active = gm.get_goal(goal.goal_group_id)
        assert still_active.status == GoalLifecycleStatus.ACTIVE
        declined = gm.get_change_proposal(proposal.id)
        assert declined.status == GoalProposalStatus.DECLINED

    def test_cannot_accept_an_already_resolved_proposal(self, gm: GoalManager) -> None:
        goal = _create_goal(gm)
        proposal = gm.create_change_proposal(
            goal.goal_group_id, GoalInterventionType.PROPOSE_ABANDONMENT, reason="reason",
            proposal_expires_at=FIXED_TIME + timedelta(days=1), now=FIXED_TIME,
        )
        gm.decline_proposal(proposal.id, now=FIXED_TIME + timedelta(hours=1))
        with pytest.raises(InvalidProposalStateError):
            gm.accept_proposal(proposal.id, now=FIXED_TIME + timedelta(hours=2))

    def test_accept_missing_proposal_raises(self, gm: GoalManager) -> None:
        with pytest.raises(GoalChangeProposalNotFoundError):
            gm.accept_proposal("does-not-exist", now=FIXED_TIME)

    def test_content_is_readable_after_creation(self, gm: GoalManager) -> None:
        goal = _create_goal(gm)
        proposal = gm.create_change_proposal(
            goal.goal_group_id, GoalInterventionType.PROPOSE_ABANDONMENT, reason="a specific recorded reason",
            proposal_expires_at=FIXED_TIME + timedelta(days=1), now=FIXED_TIME,
        )
        content = gm.get_change_proposal_content(proposal.id)
        assert content.reason == "a specific recorded reason"


class TestNoChangeAndIncreaseSupport:
    def test_accepting_no_change_leaves_goal_untouched(self, gm: GoalManager) -> None:
        goal = _create_goal(gm)
        proposal = gm.create_change_proposal(
            goal.goal_group_id, GoalInterventionType.NO_CHANGE, reason="acceptable as-is",
            proposal_expires_at=FIXED_TIME + timedelta(days=1), now=FIXED_TIME,
        )
        gm.accept_proposal(proposal.id, now=FIXED_TIME + timedelta(hours=1))
        still_active = gm.get_goal(goal.goal_group_id)
        assert still_active.status == GoalLifecycleStatus.ACTIVE
        assert still_active.current_version_id == goal.current_version_id


class TestCrashRecovery:
    def test_recover_expires_past_due_pending_proposals(self, gm: GoalManager) -> None:
        goal = _create_goal(gm)
        proposal = gm.create_change_proposal(
            goal.goal_group_id, GoalInterventionType.PROPOSE_ABANDONMENT, reason="reason",
            proposal_expires_at=FIXED_TIME + timedelta(hours=1), now=FIXED_TIME,
        )
        expired_ids = gm.recover_goal_management_state(FIXED_TIME + timedelta(hours=2))
        assert expired_ids == [proposal.id]
        expired = gm.get_change_proposal(proposal.id)
        assert expired.status == GoalProposalStatus.EXPIRED

    def test_recover_leaves_not_yet_expired_proposals_pending(self, gm: GoalManager) -> None:
        goal = _create_goal(gm)
        proposal = gm.create_change_proposal(
            goal.goal_group_id, GoalInterventionType.PROPOSE_ABANDONMENT, reason="reason",
            proposal_expires_at=FIXED_TIME + timedelta(days=7), now=FIXED_TIME,
        )
        expired_ids = gm.recover_goal_management_state(FIXED_TIME + timedelta(hours=1))
        assert expired_ids == []
        still_pending = gm.get_change_proposal(proposal.id)
        assert still_pending.status == GoalProposalStatus.PENDING

    def test_recover_is_idempotent(self, gm: GoalManager) -> None:
        goal = _create_goal(gm)
        gm.create_change_proposal(
            goal.goal_group_id, GoalInterventionType.PROPOSE_ABANDONMENT, reason="reason",
            proposal_expires_at=FIXED_TIME + timedelta(hours=1), now=FIXED_TIME,
        )
        first = gm.recover_goal_management_state(FIXED_TIME + timedelta(hours=2))
        second = gm.recover_goal_management_state(FIXED_TIME + timedelta(hours=3))
        assert len(first) == 1
        assert second == []

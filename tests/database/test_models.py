"""
tests/database/test_models.py

Confirms database/models.py's Phase 1.2 migration: created_at is a
required, keyword-only constructor parameter on every affected
dataclass — no dataclass may generate its own timestamp.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from database.models import (
    CoachAssessment,
    ConsentAction,
    ConsentRecord,
    ConsentTargetType,
    ContextSnapshot,
    ConversationMessage,
    CreatedBy,
    DecisionResult,
    KeyholderAssessment,
    MessageRole,
    ObservationRecord,
    ObservationType,
    Rule,
)

FIXED_TIME = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


class TestNoHiddenClock:
    def test_module_no_longer_exposes_utc_now(self) -> None:
        import database.models as models_module

        assert not hasattr(models_module, "utc_now"), (
            "utc_now() must be fully removed -- Clock is the only source "
            "of time now (Phase 1.2)"
        )

    @pytest.mark.parametrize(
        "build",
        [
            lambda: ContextSnapshot(created_at=FIXED_TIME),
            lambda: CoachAssessment(created_at=FIXED_TIME),
            lambda: KeyholderAssessment(created_at=FIXED_TIME),
            lambda: DecisionResult(created_at=FIXED_TIME),
            lambda: ObservationRecord(created_at=FIXED_TIME),
            lambda: Rule(created_at=FIXED_TIME),
            lambda: ConsentRecord(created_at=FIXED_TIME),
            lambda: ConversationMessage(created_at=FIXED_TIME),
        ],
    )
    def test_created_at_is_accepted_and_stored_exactly(self, build) -> None:
        instance = build()
        assert instance.created_at == FIXED_TIME

    @pytest.mark.parametrize(
        "cls",
        [
            ContextSnapshot,
            CoachAssessment,
            KeyholderAssessment,
            DecisionResult,
            ObservationRecord,
            Rule,
            ConsentRecord,
            ConversationMessage,
        ],
    )
    def test_created_at_is_required(self, cls) -> None:
        """No default -- constructing without created_at must fail, proving
        there is no hidden fallback timestamp source."""
        with pytest.raises(TypeError):
            cls()

    @pytest.mark.parametrize(
        "cls",
        [
            ContextSnapshot,
            CoachAssessment,
            KeyholderAssessment,
            DecisionResult,
            ObservationRecord,
            Rule,
            ConsentRecord,
            ConversationMessage,
        ],
    )
    def test_construction_is_keyword_only(self, cls) -> None:
        """kw_only=True -- a positional-args construction attempt must fail
        (not merely be discouraged by convention)."""
        with pytest.raises(TypeError):
            cls(FIXED_TIME)  # type: ignore[call-arg]


class TestExistingConstructionSitesStillWork:
    """Regression: the exact keyword-argument shapes already used in
    bot/discord_bot.py must keep working unchanged (only created_at is
    newly required)."""

    def test_conversation_message_construction_shape(self) -> None:
        msg = ConversationMessage(
            created_at=FIXED_TIME,
            role=MessageRole.USER,
            content="hello",
            discord_channel_id="123",
            discord_message_id="456",
        )
        assert msg.role == MessageRole.USER
        assert msg.content == "hello"

    def test_rule_construction_shape(self) -> None:
        rule = Rule(
            created_at=FIXED_TIME,
            title="Example",
            description="An example rule",
            category="general",
            created_by=CreatedBy.USER,
        )
        assert rule.title == "Example"
        assert rule.is_active is True

    def test_consent_record_construction_shape(self) -> None:
        consent = ConsentRecord(
            created_at=FIXED_TIME,
            target_type=ConsentTargetType.RULE,
            action=ConsentAction.APPROVED,
        )
        assert consent.action == ConsentAction.APPROVED

    def test_observation_record_construction_shape(self) -> None:
        obs = ObservationRecord(
            created_at=FIXED_TIME,
            observation_type=ObservationType.DECISION_MADE,
            description="something happened",
        )
        assert obs.observation_type == ObservationType.DECISION_MADE

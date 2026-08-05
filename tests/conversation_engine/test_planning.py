"""tests/conversation_engine/test_planning.py"""

from __future__ import annotations

from datetime import datetime, timezone

from ai.identity_catalog import CommunicationProfile
from conversation_engine.models import GenerationPath, ResponseCategory, ResponseContextSnapshot, SituationalConstraints
from conversation_engine.planning import build_response_plan

FIXED_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _snapshot() -> ResponseContextSnapshot:
    return ResponseContextSnapshot(
        response_category=ResponseCategory.COACHING_DIALOGUE, current_user_message="hi", language="en",
        identity_profile=CommunicationProfile(warmth=0.5, humor=0.5, teasing=0.5, assertiveness=0.5, formality=0.5, verbosity=0.5),
        situational_constraints=SituationalConstraints(), context_fragments={},
    )


class TestBuildResponsePlan:
    def test_always_coaching_dialogue(self) -> None:
        plan = build_response_plan(_snapshot())
        assert plan.response_category == ResponseCategory.COACHING_DIALOGUE

    def test_always_model_generation_path(self) -> None:
        plan = build_response_plan(_snapshot())
        assert plan.generation_path == GenerationPath.MODEL_GENERATION

    def test_no_tool_calls(self) -> None:
        plan = build_response_plan(_snapshot())
        assert plan.tool_calls == ()

    def test_no_required_provider_namespaces_in_this_slice(self) -> None:
        plan = build_response_plan(_snapshot())
        assert plan.required_provider_namespaces == frozenset()

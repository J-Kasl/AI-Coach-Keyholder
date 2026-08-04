"""tests/conversation_engine/test_validation.py"""

from __future__ import annotations

from datetime import datetime, timezone

from ai.identity_catalog import CommunicationProfile
from conversation_engine.models import (
    ConversationContextFragment,
    ConversationResponse,
    ResponseCategory,
    ResponseContextSnapshot,
    ResponsePlan,
    SituationalConstraints,
    ToolCallRequest,
)
from conversation_engine.validation import validate_response

FIXED_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _profile(**overrides) -> CommunicationProfile:
    kwargs = dict(warmth=0.5, humor=0.5, teasing=0.5, assertiveness=0.5, formality=0.5, verbosity=0.5)
    kwargs.update(overrides)
    return CommunicationProfile(**kwargs)


def _snapshot(**overrides) -> ResponseContextSnapshot:
    kwargs = dict(
        response_category=ResponseCategory.INFORMATIONAL_STATUS, current_user_message="hi", language="en",
        identity_profile=_profile(), situational_constraints=SituationalConstraints(), context_fragments={},
    )
    kwargs.update(overrides)
    return ResponseContextSnapshot(**kwargs)


class TestValidateResponse:
    def test_valid_response_passes(self) -> None:
        response = ConversationResponse(text="hello", response_category=ResponseCategory.INFORMATIONAL_STATUS)
        plan = ResponsePlan(response_category=ResponseCategory.INFORMATIONAL_STATUS)
        result = validate_response(response, plan, _snapshot())
        assert result.is_valid is True
        assert result.failure_reason is None

    def test_empty_text_fails(self) -> None:
        response = ConversationResponse(text="", response_category=ResponseCategory.INFORMATIONAL_STATUS)
        plan = ResponsePlan(response_category=ResponseCategory.INFORMATIONAL_STATUS)
        result = validate_response(response, plan, _snapshot())
        assert result.is_valid is False
        assert "empty" in result.failure_reason

    def test_whitespace_only_text_fails(self) -> None:
        response = ConversationResponse(text="   \n  ", response_category=ResponseCategory.INFORMATIONAL_STATUS)
        plan = ResponsePlan(response_category=ResponseCategory.INFORMATIONAL_STATUS)
        result = validate_response(response, plan, _snapshot())
        assert result.is_valid is False

    def test_category_mismatch_fails(self) -> None:
        response = ConversationResponse(text="hi", response_category=ResponseCategory.INFORMATIONAL_STATUS)
        plan = ResponsePlan(response_category=ResponseCategory.ERROR_FALLBACK)
        result = validate_response(response, plan, _snapshot())
        assert result.is_valid is False
        assert "category" in result.failure_reason

    def test_nonempty_tool_calls_fails(self) -> None:
        response = ConversationResponse(text="hi", response_category=ResponseCategory.INFORMATIONAL_STATUS)
        call = ToolCallRequest(tool_name="x", parameters={})
        plan = ResponsePlan(response_category=ResponseCategory.INFORMATIONAL_STATUS, tool_calls=(call,))
        result = validate_response(response, plan, _snapshot())
        assert result.is_valid is False
        assert "tool_calls" in result.failure_reason

    def test_missing_required_namespace_fails(self) -> None:
        response = ConversationResponse(text="hi", response_category=ResponseCategory.GOVERNANCE_EXPLANATION)
        plan = ResponsePlan(
            response_category=ResponseCategory.GOVERNANCE_EXPLANATION,
            required_provider_namespaces=frozenset({"decision"}),
        )
        result = validate_response(response, plan, _snapshot(response_category=ResponseCategory.GOVERNANCE_EXPLANATION))
        assert result.is_valid is False
        assert "decision" in result.failure_reason

    def test_present_required_namespace_passes(self) -> None:
        fragment = ConversationContextFragment(namespace="decision", data={})
        response = ConversationResponse(text="hi", response_category=ResponseCategory.GOVERNANCE_EXPLANATION)
        plan = ResponsePlan(
            response_category=ResponseCategory.GOVERNANCE_EXPLANATION,
            required_provider_namespaces=frozenset({"decision"}),
        )
        snapshot = _snapshot(
            response_category=ResponseCategory.GOVERNANCE_EXPLANATION, context_fragments={"decision": fragment},
        )
        result = validate_response(response, plan, snapshot)
        assert result.is_valid is True

    def test_empty_language_fails(self) -> None:
        response = ConversationResponse(text="hi", response_category=ResponseCategory.INFORMATIONAL_STATUS)
        plan = ResponsePlan(response_category=ResponseCategory.INFORMATIONAL_STATUS)
        result = validate_response(response, plan, _snapshot(language=""))
        assert result.is_valid is False
        assert "language" in result.failure_reason

    def test_empty_current_user_message_fails(self) -> None:
        response = ConversationResponse(text="hi", response_category=ResponseCategory.INFORMATIONAL_STATUS)
        plan = ResponsePlan(response_category=ResponseCategory.INFORMATIONAL_STATUS)
        result = validate_response(response, plan, _snapshot(current_user_message="   "))
        assert result.is_valid is False
        assert "current_user_message" in result.failure_reason

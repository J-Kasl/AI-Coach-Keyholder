"""tests/conversation_engine/test_engine.py"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from conversation_engine.engine import ConversationEngine
from conversation_engine.model_types import LLMGenerationError, ModelGenerationRequest
from conversation_engine.models import ResponseCategory, UnknownIdentityError
from conversation_engine.recent_history import TransitionalRecentMessageBuffer
from conversation_engine.subject_queue import SubjectConversationQueue

FIXED_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


class _FakeModel:
    def __init__(self, *, response: str | None = None, error: LLMGenerationError | None = None) -> None:
        self._response = response
        self._error = error
        self.calls: list[ModelGenerationRequest] = []

    def generate(self, *, request: ModelGenerationRequest) -> str:
        self.calls.append(request)
        if self._error is not None:
            raise self._error
        return self._response


def _engine(model) -> ConversationEngine:
    return ConversationEngine(
        model=model, buffer=TransitionalRecentMessageBuffer(max_exchanges_per_subject=5, max_characters_per_subject=5000),
        queue=SubjectConversationQueue(),
    )


class TestSuccessfulGeneration:
    def test_returns_the_models_own_text(self) -> None:
        engine = _engine(_FakeModel(response="hello there"))
        result = engine.generate_response(
            subject_key="s1", current_user_message="hi", language="en", identity_id="alex", now=FIXED_TIME,
        )
        assert result.text == "hello there"
        assert result.response_category == ResponseCategory.COACHING_DIALOGUE

    def test_successful_exchange_is_stored_atomically(self) -> None:
        buffer = TransitionalRecentMessageBuffer(max_exchanges_per_subject=5, max_characters_per_subject=5000)
        engine = ConversationEngine(model=_FakeModel(response="reply"), buffer=buffer, queue=SubjectConversationQueue())
        engine.generate_response(subject_key="s1", current_user_message="hi", language="en", identity_id="alex", now=FIXED_TIME)
        messages = buffer.get_messages(subject_key="s1")
        assert len(messages) == 2
        assert messages[0].content == "hi"
        assert messages[1].content == "reply"


class TestModelFailureFallsBack:
    def test_llm_generation_error_returns_deterministic_fallback(self) -> None:
        engine = _engine(_FakeModel(error=LLMGenerationError("ollama_timeout")))
        result = engine.generate_response(
            subject_key="s1", current_user_message="hi", language="en", identity_id="alex", now=FIXED_TIME,
        )
        assert result.response_category == ResponseCategory.ERROR_FALLBACK

    def test_fallback_does_not_touch_history(self) -> None:
        buffer = TransitionalRecentMessageBuffer(max_exchanges_per_subject=5, max_characters_per_subject=5000)
        engine = ConversationEngine(
            model=_FakeModel(error=LLMGenerationError("ollama_timeout")), buffer=buffer, queue=SubjectConversationQueue(),
        )
        engine.generate_response(subject_key="s1", current_user_message="hi", language="en", identity_id="alex", now=FIXED_TIME)
        assert buffer.get_messages(subject_key="s1") == ()


class TestValidationFailureFallsBack:
    def test_empty_model_output_falls_back(self) -> None:
        engine = _engine(_FakeModel(response=""))
        # The Ollama adapter itself would reject empty text before returning,
        # but engine.py's own validate_response() is a second, independent check --
        # simulate a model that somehow bypasses the adapter's own validation.
        result = engine.generate_response(
            subject_key="s1", current_user_message="hi", language="en", identity_id="alex", now=FIXED_TIME,
        )
        assert result.response_category == ResponseCategory.ERROR_FALLBACK

    def test_validation_failure_does_not_touch_history(self) -> None:
        buffer = TransitionalRecentMessageBuffer(max_exchanges_per_subject=5, max_characters_per_subject=5000)
        engine = ConversationEngine(model=_FakeModel(response=""), buffer=buffer, queue=SubjectConversationQueue())
        engine.generate_response(subject_key="s1", current_user_message="hi", language="en", identity_id="alex", now=FIXED_TIME)
        assert buffer.get_messages(subject_key="s1") == ()


class TestIdentityHandling:
    def test_unknown_identity_id_raises_not_caught_here(self) -> None:
        """ConversationEngine itself does not catch UnknownIdentityError
        -- ApplicationService's own integration is responsible for
        checking identity_id is None/unknown BEFORE calling this method
        (see application/service.py)."""
        engine = _engine(_FakeModel(response="should not be reached"))
        with pytest.raises(UnknownIdentityError):
            engine.generate_response(
                subject_key="s1", current_user_message="hi", language="en",
                identity_id="not-a-real-identity", now=FIXED_TIME,
            )


class TestGenerationPathIsAlwaysModelGeneration:
    def test_the_model_is_actually_invoked_for_the_unmatched_path(self) -> None:
        model = _FakeModel(response="ok")
        engine = _engine(model)
        engine.generate_response(subject_key="s1", current_user_message="hi", language="en", identity_id="alex", now=FIXED_TIME)
        assert len(model.calls) == 1


class TestNoDomainWriteAPICalled:
    def test_a_model_response_claiming_an_action_never_calls_any_write_api(self) -> None:
        """The model's own text is just text -- ConversationEngine has
        no reference to AdvancedModeAdministration/PenaltyEngine/etc.
        at all, structurally, not just behaviorally."""
        engine = _engine(_FakeModel(response="I switched you to Advanced Mode."))
        result = engine.generate_response(
            subject_key="s1", current_user_message="switch me to advanced", language="en",
            identity_id="alex", now=FIXED_TIME,
        )
        assert result.text == "I switched you to Advanced Mode."  # returned as plain text, nothing executed
        assert not hasattr(engine, "advanced_mode_admin")
        assert not hasattr(engine, "penalty_engine")

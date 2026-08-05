"""
conversation_engine/engine.py

ConversationEngine -- Slice 2's own orchestration entry point. The
ONLY place in this package that logs an expected model/validation
failure (ConversationModel implementations only ever raise a typed
LLMGenerationError; they never log anything themselves).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Sequence

from conversation_engine.context import build_response_context
from conversation_engine.fallback import FallbackReason, render_fallback
from conversation_engine.model_types import ConversationModel, LLMGenerationError
from conversation_engine.models import ConversationResponse, ResponseCategory, SituationalConstraints
from conversation_engine.planning import build_response_plan
from conversation_engine.prompt_builder import build_generation_request
from conversation_engine.providers import ConversationContextProvider
from conversation_engine.recent_history import TransitionalRecentMessageBuffer
from conversation_engine.subject_queue import SubjectConversationQueue
from conversation_engine.validation import validate_response

__all__ = ["ConversationEngine", "DEFAULT_MAX_OUTPUT_CHARACTERS"]

logger = logging.getLogger("ai_coach_keyholder.conversation_engine")

DEFAULT_MAX_OUTPUT_CHARACTERS = 1800


class ConversationEngine:
    """
    Composition-root constructed only -- never builds its own
    ConversationModel/buffer/queue (dependency injection, per explicit
    review decision). `generate_response()` is the single call
    ApplicationService makes for the unmatched-text path.
    """

    def __init__(
        self, *, model: ConversationModel, buffer: TransitionalRecentMessageBuffer,
        queue: SubjectConversationQueue, providers: Sequence[ConversationContextProvider] = (),
        max_output_characters: int = DEFAULT_MAX_OUTPUT_CHARACTERS,
    ) -> None:
        self._model = model
        self._buffer = buffer
        self._queue = queue
        self._providers = providers
        self._max_output_characters = max_output_characters

    def generate_response(
        self, *, subject_key: str, current_user_message: str, language: str, identity_id: str, now: datetime,
    ) -> ConversationResponse:
        with self._queue.turn(subject_key):
            recent = self._buffer.get_messages(subject_key=subject_key)

            outcome = build_response_context(
                response_category=ResponseCategory.COACHING_DIALOGUE,
                current_user_message=current_user_message, language=language, identity_id=identity_id,
                situational_constraints=SituationalConstraints(), providers=self._providers,
                required_provider_namespaces=frozenset(), now=now,
            )
            if outcome.fallback_response is not None:
                return outcome.fallback_response  # history is NOT touched

            plan = build_response_plan(outcome.snapshot)
            request = build_generation_request(
                snapshot=outcome.snapshot, plan=plan, recent_messages=recent,
                max_output_characters=self._max_output_characters,
            )

            try:
                raw_text = self._model.generate(request=request)
            except LLMGenerationError as exc:
                logger.warning(
                    "Conversation Engine generation failed",
                    extra={
                        "error_code": exc.code, "adapter": type(self._model).__name__,
                        "response_category": plan.response_category.value,
                    },
                )
                return render_fallback(FallbackReason.GENERATION_UNAVAILABLE, language=language)

            response = ConversationResponse(text=raw_text, response_category=plan.response_category)
            validation = validate_response(response, plan, outcome.snapshot)
            if not validation.is_valid:
                logger.warning(
                    "Conversation Engine response validation failed",
                    extra={
                        "error_code": "validation_failed", "adapter": type(self._model).__name__,
                        "response_category": plan.response_category.value,
                    },
                )
                return render_fallback(FallbackReason.GENERATION_UNAVAILABLE, language=language)

            # Only a fully successful, validated exchange is stored --
            # atomic (the whole pair, or nothing) and never on a fallback path.
            self._buffer.append_exchange(
                subject_key=subject_key, user_text=current_user_message, assistant_text=response.text,
            )
            return response

"""
conversation_engine/engine.py

ConversationEngine -- Slice 3's own orchestration entry point, now
reading/writing through memory_system's WorkingMemoryReader/
WorkingMemoryWriter instead of the retired TransitionalRecentMessageBuffer.
The ONLY place in this package that logs an expected model/validation/
memory failure (ConversationModel implementations only ever raise a
typed LLMGenerationError; memory_system itself never logs anything).

Read failure policy: an expected WorkingMemoryError (or an unexpected
exception) during read() is logged once, then the engine proceeds with
an EMPTY history -- Working Memory turns are not authoritative domain
facts, so an empty history is a safe degradation, not a fabricated one
(the same state a brand-new subject's first message already has).

Write failure policy: if generation and validation both succeeded but
commit_exchange() fails (expected WorkingMemoryError,
WorkingMemoryCapacityError, or an unexpected exception), the already-
validated response is still returned to the user -- discarding a safe,
correct response over an internal memory-continuity failure would be
disproportionate. Logged once. No retry, no partial commit. The next
turn simply won't see this exchange in its history -- a real but
bounded consequence.

Expected vs. unexpected failures get DIFFERENT log codes -- an
unexpected exception must never be logged as if it were an ordinary,
anticipated capacity/read/commit condition.
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
from conversation_engine.subject_queue import SubjectConversationQueue
from conversation_engine.validation import validate_response
from memory_system.models import WorkingMemoryCapacityError, WorkingMemoryError, WorkingMemorySnapshot
from memory_system.working_memory import WorkingMemoryReader, WorkingMemoryWriter

__all__ = ["ConversationEngine", "DEFAULT_MAX_OUTPUT_CHARACTERS"]

logger = logging.getLogger("ai_coach_keyholder.conversation_engine")

DEFAULT_MAX_OUTPUT_CHARACTERS = 1800


class ConversationEngine:
    """
    Composition-root constructed only -- never builds its own
    ConversationModel/WorkingMemoryReader/WorkingMemoryWriter/queue
    (dependency injection, per explicit review decision).
    `generate_response()` is the single call ApplicationService makes
    for the unmatched-text path.
    """

    def __init__(
        self, *, model: ConversationModel,
        working_memory_reader: WorkingMemoryReader, working_memory_writer: WorkingMemoryWriter,
        queue: SubjectConversationQueue, providers: Sequence[ConversationContextProvider] = (),
        max_output_characters: int = DEFAULT_MAX_OUTPUT_CHARACTERS,
    ) -> None:
        self._model = model
        self._working_memory_reader = working_memory_reader
        self._working_memory_writer = working_memory_writer
        self._queue = queue
        self._providers = providers
        self._max_output_characters = max_output_characters

    def generate_response(
        self, *, subject_key: str, current_user_message: str, language: str, identity_id: str, now: datetime,
    ) -> ConversationResponse:
        with self._queue.turn(subject_key):
            working_memory_snapshot = self._read_working_memory(subject_key)

            outcome = build_response_context(
                response_category=ResponseCategory.COACHING_DIALOGUE,
                current_user_message=current_user_message, language=language, identity_id=identity_id,
                situational_constraints=SituationalConstraints(), providers=self._providers,
                required_provider_namespaces=frozenset(), now=now,
            )
            if outcome.fallback_response is not None:
                return outcome.fallback_response  # Working Memory is NOT touched

            plan = build_response_plan(outcome.snapshot)
            request = build_generation_request(
                snapshot=outcome.snapshot, plan=plan, working_memory_turns=working_memory_snapshot.turns,
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

            # Only a fully successful, validated exchange is committed --
            # never on a fallback path. Write failure policy: the
            # already-validated response is still returned regardless
            # (see this module's own docstring).
            self._commit_exchange(subject_key, current_user_message, response.text)
            return response

    def _read_working_memory(self, subject_key: str) -> WorkingMemorySnapshot:
        try:
            return self._working_memory_reader.read(subject_key=subject_key)
        except WorkingMemoryError:
            logger.warning(
                "Conversation Engine working memory read failed",
                extra={
                    "error_code": "working_memory_read_failed", "operation": "read",
                    "component": type(self._working_memory_reader).__name__,
                },
            )
        except Exception:
            logger.warning(
                "Conversation Engine working memory read raised an unexpected error",
                extra={
                    "error_code": "working_memory_unexpected_read_error", "operation": "read",
                    "component": type(self._working_memory_reader).__name__,
                },
            )
        return WorkingMemorySnapshot(turns=())  # safe degradation -- an empty history is already a normal state

    def _commit_exchange(self, subject_key: str, user_content: str, assistant_content: str) -> None:
        try:
            self._working_memory_writer.commit_exchange(
                subject_key=subject_key, user_content=user_content, assistant_content=assistant_content,
            )
        except WorkingMemoryCapacityError:
            logger.warning(
                "Conversation Engine working memory commit exceeded capacity",
                extra={
                    "error_code": "working_memory_capacity_exceeded", "operation": "commit",
                    "component": type(self._working_memory_writer).__name__,
                },
            )
        except WorkingMemoryError:
            logger.warning(
                "Conversation Engine working memory commit failed",
                extra={
                    "error_code": "working_memory_commit_failed", "operation": "commit",
                    "component": type(self._working_memory_writer).__name__,
                },
            )
        except Exception:
            logger.warning(
                "Conversation Engine working memory commit raised an unexpected error",
                extra={
                    "error_code": "working_memory_unexpected_commit_error", "operation": "commit",
                    "component": type(self._working_memory_writer).__name__,
                },
            )
        # In every case above, the already-validated response returned to
        # the caller is unaffected -- the write failure policy this
        # module's own docstring documents.

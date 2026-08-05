"""
conversation_engine/model_types.py

The boundary between Conversation Engine's own types (ResponseContextSnapshot,
ResponsePlan, ConversationResponse, ...) and the model adapter.
`ConversationModel` implementations (e.g. OllamaConversationModel) know
NOTHING about snapshots, plans, the recent-history buffer, or
ConversationResponse -- only these three small, self-contained types.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

__all__ = ["ModelMessageRole", "ModelMessage", "ModelGenerationRequest", "ConversationModel", "LLMGenerationError"]


class LLMGenerationError(RuntimeError):
    """The ONLY exception type a ConversationModel implementation may
    let escape its own `generate()`. `code` is a short, sanitized,
    machine-readable identifier -- never a prompt, a response body, or
    any user/context data (see ConversationEngine's own single logging
    point, engine.py)."""
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ModelMessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


@dataclass(frozen=True, kw_only=True)
class ModelMessage:
    role: ModelMessageRole
    content: str


@dataclass(frozen=True, kw_only=True)
class ModelGenerationRequest:
    messages: tuple[ModelMessage, ...]
    max_output_characters: int


class ConversationModel(Protocol):
    """A structural Protocol, matching this project's own established
    precedent (infrastructure/clock.py's Clock, conversation_engine/
    providers.py's ConversationContextProvider). Returns a validated,
    already-bounded raw string -- never raises anything other than
    LLMGenerationError (or a subclass) at its own boundary."""

    def generate(self, *, request: ModelGenerationRequest) -> str: ...

"""
conversation_engine/fallback.py

The deterministic fallback renderer -- CE-4/CE-20. Pure Python, no
provider calls, no LLM, no dependency on anything else in this package
that could itself fail (deliberately does not import context.py, to
avoid even the possibility of a circular "fallback needs assembly
which needs fallback" failure mode). Must work even when providers
fail, validation fails, the engine is unconfigured, or a future LLM
path is unavailable.

Text is English-only in this slice, matching every other command
response in this project today (application/service.py,
application/onboarding_service.py) -- no new localization framework.
"""

from __future__ import annotations

from enum import StrEnum

from conversation_engine.models import ConversationResponse, ResponseCategory

__all__ = ["FallbackReason", "render_fallback"]


class FallbackReason(StrEnum):
    GENERIC = "generic"
    MISSING_REQUIRED_CONTEXT = "missing_required_context"
    UNSUPPORTED_CATEGORY = "unsupported_category"
    GENERATION_UNAVAILABLE = "generation_unavailable"


_MESSAGES: dict[FallbackReason, str] = {
    FallbackReason.GENERIC: "Something went wrong putting that response together. It's been logged.",
    FallbackReason.MISSING_REQUIRED_CONTEXT: "I don't have enough information to answer that right now.",
    FallbackReason.UNSUPPORTED_CATEGORY: "I can't handle that kind of response yet.",
    FallbackReason.GENERATION_UNAVAILABLE: "I can't generate a response right now. Please try again shortly.",
}


def render_fallback(reason: FallbackReason = FallbackReason.GENERIC, *, language: str = "en") -> ConversationResponse:
    """
    CE-4: reachable through a path that does not depend on any other
    part of this engine being available. `language` is accepted for
    forward-compatibility with a real localization decision later
    (conversation_engine_technical_design.md Section 5's own note on
    ONBOARDING/ERROR_FALLBACK) -- currently ignored, since no
    localization framework for response text exists anywhere in this
    project yet (application/onboarding_service.py's own Section 5
    limit). Never raises.
    """
    text = _MESSAGES.get(reason, _MESSAGES[FallbackReason.GENERIC])
    return ConversationResponse(text=text, response_category=ResponseCategory.ERROR_FALLBACK)

"""
conversation_engine/prompt_builder.py

Assembles a ModelGenerationRequest from a ResponseContextSnapshot,
ResponsePlan, and Working Memory turns (memory_system.models.WorkingMemoryTurn
-- immutable types only; this module never imports InMemoryWorkingMemory
or any other concrete storage implementation). The system message is
produced ENTIRELY by deterministic code -- the user's own content is
never concatenated into it. Role separation (a distinct role="user"
message, always) is a structural guarantee, not a textual convention:
user content cannot physically alter the system message, because it
never occupies the same field.

Role separation guarantees that user content cannot physically alter
the deterministically assembled system message. It does not guarantee
that the model can never be influenced by malicious user content.
Slice 2 does not implement a semantic prompt-injection detector; this
remains true after Slice 3's own Working Memory integration.
"""

from __future__ import annotations

from conversation_engine.model_types import ModelGenerationRequest, ModelMessage, ModelMessageRole
from conversation_engine.models import ResponseCategory, ResponseContextSnapshot, ResponsePlan
from memory_system.models import WorkingMemoryRole, WorkingMemoryTurn

__all__ = ["build_generation_request"]

# Section "Conversation Engine MUST NOT" (conversation_engine_technical_design.md
# CE-11 through CE-19), folded into the system message's own first layer
# per explicit review decision: these are the model's fixed, never-negotiable
# operating boundaries, not something any ResponseCategory can vary.
_SYSTEM_BOUNDARIES = (
    "You are the AI voice of a personal accountability coaching system. "
    "You must never: change or invent a decision the system has already made; "
    "perform, track, or bypass any consent or confirmation process; "
    "claim a fact the system does not actually know, including inferring one from silence; "
    "disclose any internal scoring, weighting, or token-economy state; "
    "override a stated safety or situational constraint for any reason; "
    "imply an Advanced-Mode-only capability is available when it is not; "
    "claim to have taken, or take, any action -- you can only describe, in words, what a user could do. "
    "You have no ability to change any setting, mode, or record. Any statement you make about "
    "having done so is not true and must not be treated as having happened."
)

# Category-specific instructions -- distinct from the boundaries above.
# Slice 2 only ever uses COACHING_DIALOGUE for the unmatched-text path.
_CATEGORY_INSTRUCTIONS = {
    ResponseCategory.COACHING_DIALOGUE: (
        "Respond as a supportive, direct accountability coach having an ordinary conversation. "
        "Keep responses concise and focused on what the user actually said."
    ),
}


def _build_system_message(snapshot: ResponseContextSnapshot, plan: ResponsePlan) -> str:
    category_text = _CATEGORY_INSTRUCTIONS.get(plan.response_category, "")
    identity = snapshot.identity_profile
    identity_text = (
        f"Tone guidance -- warmth {identity.warmth:.1f}, humor {identity.humor:.1f}, "
        f"teasing {identity.teasing:.1f}, assertiveness {identity.assertiveness:.1f}, "
        f"formality {identity.formality:.1f}, verbosity {identity.verbosity:.1f} (0=low, 1=high)."
    )
    language_text = f"Respond in this language code: {snapshot.language}."
    # No domain/memory context section in Slice 2 -- no provider ships
    # concrete data yet, so there is nothing to insert; an empty
    # section is never emitted (per explicit review instruction).
    parts = [_SYSTEM_BOUNDARIES, category_text, identity_text, language_text]
    return "\n\n".join(p for p in parts if p)


def build_generation_request(
    *, snapshot: ResponseContextSnapshot, plan: ResponsePlan,
    working_memory_turns: tuple[WorkingMemoryTurn, ...], max_output_characters: int,
) -> ModelGenerationRequest:
    system_text = _build_system_message(snapshot, plan)
    messages: list[ModelMessage] = [ModelMessage(role=ModelMessageRole.SYSTEM, content=system_text)]

    for turn in working_memory_turns:
        role = ModelMessageRole.USER if turn.role == WorkingMemoryRole.USER else ModelMessageRole.ASSISTANT
        messages.append(ModelMessage(role=role, content=turn.content))

    # The current user message is ALWAYS the last, separate role="user"
    # turn -- never concatenated into the system message above.
    messages.append(ModelMessage(role=ModelMessageRole.USER, content=snapshot.current_user_message))

    return ModelGenerationRequest(messages=tuple(messages), max_output_characters=max_output_characters)

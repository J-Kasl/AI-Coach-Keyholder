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

Slice D adds a deterministic "AUTHORITATIVE APPLICATION STATE" section,
built from `snapshot.context_fragments` -- current domain facts read
fresh from lock_state/task_runtime for this turn, placed BEFORE Working
Memory in the system message. Template metadata (category, difficulty,
completion_requirements, ...) is serialized as plain, labeled DATA
inside this one block -- it never becomes a new ModelMessage, never
changes any message's role, and never gets concatenated into
_SYSTEM_BOUNDARIES or any other instruction text. A namespace/field this
module does not explicitly know how to render is silently skipped, not
guessed at.
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
#
# The command list below (First Testable Keyholder Milestone Slice C/D
# follow-up: Conversation Behavior/Prompt Policy) is EXHAUSTIVE and
# EXACT -- the model must never invent command syntax outside it. Kept
# here, not derived from CommandRouter at runtime, because this text
# is deterministic, reviewed, and versioned the same way every other
# part of this module is -- introducing a dynamic dependency on the
# router's own registered set would make this file's own prompt output
# depend on registration order/content elsewhere, which is exactly the
# kind of coupling this module's own docstring already avoids.
_KNOWN_COMMANDS = (
    "lock status", "lock report locked", "lock report unlocked",
    "task request", "task active", "task complete", "task cancel", "help",
)

_CATEGORY_INSTRUCTIONS = {
    ResponseCategory.COACHING_DIALOGUE: (
        "Respond as a supportive, direct accountability coach having an ordinary conversation. "
        "Keep responses concise and focused on what the user actually said.\n"
        "When an authoritative application state section is present below, use it naturally when relevant to "
        "what the user is saying -- do not mechanically repeat it if it isn't relevant.\n"
        "Lock-state epistemics are exact and must never be upgraded: UNKNOWN means no report is "
        "recorded -- never infer the user is unlocked. LOCKED_USER_REPORTED and UNLOCKED_USER_REPORTED "
        "mean only that the user reported that state -- never claim the physical device has been "
        "independently verified, regardless of which way the report goes.\n"
        "An active task is authoritative application state: you may coach around it, but "
        "conversational statements like \"I finished it\" or \"done\" do NOT change its recorded "
        "state, and you must never claim they did.\n"
        "Conversational acknowledgement is not a state transition: saying you understand, or that "
        "something sounds done, never performs any change -- only the deterministic commands below do.\n"
        "The only commands that exist are exactly these -- never invent different wording or syntax: "
        + ", ".join(f"`{c}`" for c in _KNOWN_COMMANDS) + ".\n"
        "When the user's own intent matches one of these, mention the specific command naturally, only "
        "when it's actually relevant -- do not force a recommendation into unrelated conversation: "
        "reporting locked -> `lock report locked`; reporting unlocked -> `lock report unlocked`; "
        "requesting a task -> `task request`; completing the active task -> `task complete`; "
        "cancelling the active task -> `task cancel`; checking current lock state -> `lock status`; "
        "checking the active task -> `task active`; finding other commands -> `help`. "
        "The user decides whether to actually invoke a command -- do not imply `task complete` is "
        "warranted merely because you believe the task is done.\n"
        "You can only describe these commands in words; your own text never executes them.\n"
        "If the active task's own recorded details are sparse, work with what is actually there -- "
        "never invent task instructions that were not provided."
    ),
}


_DOMAIN_CONTEXT_PREAMBLE = (
    "AUTHORITATIVE APPLICATION STATE\n"
    "These facts come from the application's current domain records. "
    "They take precedence over conflicting conversational history. "
    "They do not imply physical verification unless explicitly stated."
)


def _render_lock_state(data: object) -> str:
    return f"Lock: {data['status']}. {data['note']}"


def _render_active_task(data: object) -> str:
    if not data["has_active_task"]:
        return "Active task: none."
    return (
        f"Active task: template_id={data['template_id']}, version={data['template_version']}, "
        f"category={data['category']}, difficulty={data['difficulty']}, "
        f"duration_minutes={data['duration_minutes']}, "
        f"completion_requirements={data['completion_requirements']!r}."
    )


# Deterministic order + only the namespaces this module explicitly
# knows how to render -- an unrecognized namespace is silently
# skipped, never guessed at or dumped raw.
_FRAGMENT_RENDERERS = {
    "lock_state": _render_lock_state,
    "active_task": _render_active_task,
}
_FRAGMENT_ORDER = ("lock_state", "active_task")


def _build_domain_context_section(snapshot: ResponseContextSnapshot) -> str:
    lines = [
        _FRAGMENT_RENDERERS[namespace](snapshot.context_fragments[namespace].data)
        for namespace in _FRAGMENT_ORDER
        if namespace in snapshot.context_fragments
    ]
    if not lines:
        return ""
    return _DOMAIN_CONTEXT_PREAMBLE + "\n" + "\n".join(lines)


def _build_system_message(snapshot: ResponseContextSnapshot, plan: ResponsePlan) -> str:
    category_text = _CATEGORY_INSTRUCTIONS.get(plan.response_category, "")
    identity = snapshot.identity_profile
    identity_text = (
        f"Tone guidance -- warmth {identity.warmth:.1f}, humor {identity.humor:.1f}, "
        f"teasing {identity.teasing:.1f}, assertiveness {identity.assertiveness:.1f}, "
        f"formality {identity.formality:.1f}, verbosity {identity.verbosity:.1f} (0=low, 1=high)."
    )
    language_text = f"Respond in this language code: {snapshot.language}."
    domain_context_text = _build_domain_context_section(snapshot)
    # An empty domain context section is never emitted -- only present
    # when at least one recognized fragment actually reached the snapshot.
    parts = [_SYSTEM_BOUNDARIES, category_text, identity_text, domain_context_text, language_text]
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

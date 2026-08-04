"""
conversation_engine/validation.py

Purely structural checks -- no semantic Explanation Fidelity
comparison (ID-3), no natural-language understanding, no LLM, no
retry/repair. That is explicitly later-slice work
(conversation_engine_technical_design.md Section 19, Open Questions 4
and 5) -- this module does not pretend otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass

from conversation_engine.models import ConversationResponse, GenerationPath, ResponseContextSnapshot, ResponsePlan

__all__ = ["ValidationResult", "validate_response"]


@dataclass(frozen=True, kw_only=True)
class ValidationResult:
    is_valid: bool
    failure_reason: str | None = None


def validate_response(
    response: ConversationResponse, plan: ResponsePlan, snapshot: ResponseContextSnapshot,
) -> ValidationResult:
    """
    Structural contract only -- every check here is a shape/contract
    check, never a semantic judgment about what the text actually says:

    - `response.text` is non-empty after `strip()`
    - `response.response_category` matches `plan.response_category`
    - `plan.generation_path` is a real `GenerationPath` member (defense
      in depth against a plan constructed from untyped data, e.g. a
      dict deserialized without going through the dataclass itself)
    - `plan.tool_calls` is empty (CE-21 -- no tool calling in this slice)
    - `plan.required_provider_namespaces`/`optional_provider_namespaces`
      do not overlap (also enforced by `ResponsePlan.__post_init__`;
      checked again here since a plan and a snapshot can be constructed
      independently and only paired at validation time)
    - every `plan.required_provider_namespaces` entry is present in
      `snapshot.context_fragments`
    - `snapshot.language`/`snapshot.current_user_message` are non-empty
      after `strip()`
    - every `context_fragments` key equals its own `fragment.namespace`
      (also enforced by `ResponseContextSnapshot.__post_init__`;
      checked again here for the same reason as the namespace-overlap
      check above)
    """
    if not response.text.strip():
        return ValidationResult(is_valid=False, failure_reason="response text is empty")

    if response.response_category != plan.response_category:
        return ValidationResult(
            is_valid=False,
            failure_reason=(
                f"response category {response.response_category.value!r} does not match "
                f"plan category {plan.response_category.value!r}"
            ),
        )

    if not isinstance(plan.generation_path, GenerationPath):
        return ValidationResult(is_valid=False, failure_reason="generation_path is not a valid GenerationPath member")

    if plan.tool_calls:
        return ValidationResult(is_valid=False, failure_reason="tool_calls must be empty in this slice")

    overlap = plan.required_provider_namespaces & plan.optional_provider_namespaces
    if overlap:
        return ValidationResult(
            is_valid=False, failure_reason=f"required/optional namespaces overlap: {sorted(overlap)!r}",
        )

    missing = plan.required_provider_namespaces - snapshot.context_fragments.keys()
    if missing:
        return ValidationResult(
            is_valid=False, failure_reason=f"required namespace(s) missing from snapshot: {sorted(missing)!r}",
        )

    if not snapshot.language.strip():
        return ValidationResult(is_valid=False, failure_reason="snapshot language is empty")

    if not snapshot.current_user_message.strip():
        return ValidationResult(is_valid=False, failure_reason="snapshot current_user_message is empty")

    for key, fragment in snapshot.context_fragments.items():
        if key != fragment.namespace:
            return ValidationResult(
                is_valid=False,
                failure_reason=f"context_fragments key {key!r} does not match fragment.namespace {fragment.namespace!r}",
            )

    return ValidationResult(is_valid=True)

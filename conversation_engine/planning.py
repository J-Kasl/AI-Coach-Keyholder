"""
conversation_engine/planning.py

Deterministic Response Planning (pipeline stage 2). Slice 2's own
unmatched-text path always plans ResponseCategory.COACHING_DIALOGUE
via GenerationPath.MODEL_GENERATION -- the plan represents the
INTENDED path, never retroactively rewritten to DETERMINISTIC_FALLBACK
if the model later fails (that is a fallback RESPONSE, not a changed
plan -- see engine.py).
"""

from __future__ import annotations

from conversation_engine.models import GenerationPath, ResponseCategory, ResponseContextSnapshot, ResponsePlan

__all__ = ["build_response_plan"]


def build_response_plan(snapshot: ResponseContextSnapshot) -> ResponsePlan:
    """Never initiates a workflow/tool call (CE-21 -- tool_calls stays
    empty, the field's own default). No provider namespace is required
    in this slice (no domain/memory provider ships concrete data yet)."""
    return ResponsePlan(
        response_category=ResponseCategory.COACHING_DIALOGUE,
        generation_path=GenerationPath.MODEL_GENERATION,
    )

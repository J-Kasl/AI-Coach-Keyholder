"""
task_runtime/selection.py

select_eligible_template() -- a deliberately trivial, deterministic
placeholder selection, not scoring/ranking. Neither get_active_templates()
nor get_eligible_templates() carries an explicit ORDER BY (verified
directly against both queries), so their return order is not a
guaranteed-deterministic property of the query itself -- this function
imposes a stable, explicit order in Python instead of touching either
existing, already-tested SQL query.
"""

from __future__ import annotations

from task_catalog.models import TaskTemplateVersion

__all__ = ["select_eligible_template"]


def select_eligible_template(eligible: tuple[TaskTemplateVersion, ...]) -> TaskTemplateVersion | None:
    """
    Deterministic: lowest template_id, alphabetically. Trivially
    replaceable later (ranking/personality/preference) -- this is a
    placeholder selection, not scoring. `None` for an empty input,
    never an error (no eligible template is a normal, expected state).
    """
    if not eligible:
        return None
    return sorted(eligible, key=lambda t: t.template_id)[0]

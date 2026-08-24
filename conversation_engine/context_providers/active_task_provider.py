"""
conversation_engine/context_providers/active_task_provider.py

ActiveTaskContextProvider -- a ConversationContextProvider reading
only through task_runtime's own TaskRuntime and task_catalog's own
TaskCatalog, never TaskRuntimeAdministration/TaskCatalogAdministration.
Always returns a real fragment on a successful read, including
`has_active_task=False` -- None/an exception is reserved exclusively
for a genuine read failure.

Uses the assignment's own exact (template_id, template_version) --
never the template's current/latest version -- so the prompt always
reflects the immutable version the assignment was actually created
against, even if Task Catalog's own current_version has since advanced.
"""

from __future__ import annotations

from datetime import datetime

from conversation_engine.models import ConversationContextFragment
from task_catalog.repository import TaskCatalog
from task_runtime.repository import TaskRuntime

__all__ = ["ActiveTaskContextProvider"]

# Minimal disclosure whitelist -- title/instructions (Candidate B,
# migration 021) are now the primary human-readable content; the
# previously-documented "no user-facing title/description field
# exists yet" limitation is closed by this fragment. template_id is
# still included alongside title -- it remains the only STABLE
# conversational/support reference (title wording can change across
# versions; template_id does not), and it was never in this module's
# own "never expose" list to begin with (only assignment-/user-scoped
# identifiers are). template_version is retained for the same
# historical-accuracy reason as before. Never included: assignment
# id, user_id, any consent/provenance id, or raw audit timestamps not
# needed for the conversation.
#
# title/instructions are exposed as `str | None` -- `None` only for a
# legacy row that predates migration 021 (task_catalog/models.py's own
# TaskTemplateVersion docstring). This provider does not fabricate
# replacement text for a `None` value; rendering an explicit "not
# recorded" marker for that case is conversation_engine/prompt_builder.py's
# job, not this provider's.


class ActiveTaskContextProvider:
    """Stateless, static -- see LockStateContextProvider's own
    docstring for why this is safe to share across subjects."""

    namespace = "active_task"

    def __init__(self, *, task_runtime: TaskRuntime, task_catalog: TaskCatalog) -> None:
        self._task_runtime = task_runtime
        self._task_catalog = task_catalog

    def provide_context(self, *, subject_key: str, now: datetime) -> ConversationContextFragment | None:
        assignment = self._task_runtime.get_active_assignment(subject_key)
        if assignment is None:
            return ConversationContextFragment(namespace=self.namespace, data={"has_active_task": False})

        template = self._task_catalog.get_template(assignment.template_id, assignment.template_version)
        if template is None:
            # The assignment's own composite FK guarantees this row
            # exists (task_runtime/README.md) -- reaching here would be
            # a genuine, unexpected inconsistency, not a normal
            # "no active task" state. Treat it as a read failure, not
            # a fabricated negative fact.
            return None

        return ConversationContextFragment(
            namespace=self.namespace,
            data={
                "has_active_task": True,
                "template_id": template.template_id,
                "template_version": template.version,
                "title": template.title,
                "instructions": template.instructions,
                "category": template.category,
                "difficulty": template.difficulty,
                "duration_minutes": template.duration_minutes,
                "completion_requirements": template.completion_requirements,
            },
        )

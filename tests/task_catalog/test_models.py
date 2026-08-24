"""tests/task_catalog/test_models.py"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import pytest

from task_catalog.models import (
    LockRequirement,
    TaskInstanceRole,
    TaskTemplateCatalogEntry,
    TaskTemplateEligibilityStatus,
    TaskTemplateVersion,
)

FIXED_TIME = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _version(**overrides) -> TaskTemplateVersion:
    kwargs = dict(
        template_id="tmpl-1", version=1, category="chore", difficulty="easy", effort="low",
        duration_minutes=10, required_equipment=(), required_privacy="none", required_context="home",
        safety_classification="safe", eligible_instance_roles=(TaskInstanceRole.RECOVERY,),
        eligible_operating_modes=("standard",), completion_requirements={}, verification_requirements={},
        reflection_requirements=None, lock_requirement=LockRequirement.NONE,
        created_at=FIXED_TIME, created_via_consent_id="consent-1",
    )
    kwargs.update(overrides)
    return TaskTemplateVersion(**kwargs)


class TestTaskInstanceRole:
    def test_all_five_roles_exist(self) -> None:
        assert {r.value for r in TaskInstanceRole} == {
            "recovery", "primary", "journaling", "integrity", "optional_challenge",
        }


class TestTaskTemplateEligibilityStatus:
    def test_both_states_exist(self) -> None:
        assert {s.value for s in TaskTemplateEligibilityStatus} == {"active", "deactivated"}


class TestTaskTemplateVersionImmutability:
    """TC-1, enforced at the Python level, not only documented."""

    def test_is_frozen(self) -> None:
        version = _version()
        with pytest.raises(dataclasses.FrozenInstanceError):
            version.category = "changed"  # type: ignore[misc]

    def test_id_defaults_to_a_generated_value(self) -> None:
        a = _version()
        b = _version()
        assert a.id != b.id


class TestTaskTemplateVersionValidation:
    """Point 4 of the requested review: minimal validation, confirmed
    previously missing entirely (empty/duplicate values wrote
    successfully with no error)."""

    def test_empty_eligible_instance_roles_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="eligible_instance_roles must not be empty"):
            _version(eligible_instance_roles=())

    def test_duplicate_eligible_instance_roles_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="eligible_instance_roles must not contain duplicates"):
            _version(eligible_instance_roles=(TaskInstanceRole.RECOVERY, TaskInstanceRole.RECOVERY))

    def test_empty_eligible_operating_modes_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="eligible_operating_modes must not be empty"):
            _version(eligible_operating_modes=())

    def test_duplicate_eligible_operating_modes_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="eligible_operating_modes must not contain duplicates"):
            _version(eligible_operating_modes=("standard", "standard"))

    def test_a_valid_version_still_constructs_normally(self) -> None:
        version = _version()  # the default fixture -- must not raise
        assert version.eligible_instance_roles == (TaskInstanceRole.RECOVERY,)


class TestTaskTemplateVersionTitleInstructions:
    """Candidate B (migration 021). `None` is the legacy/pre-migration
    "not recorded" state and must remain constructible -- see this
    module's own TaskTemplateVersion docstring. A non-`None` value,
    however, must be real: not blank, not whitespace-only, not over
    the approved length. The stronger "a NEWLY CREATED version must
    never be None" rule belongs to the write API
    (TaskCatalogAdministration.create_template()/add_version(), see
    tests/task_catalog/test_repository.py), not to this dataclass."""

    def test_title_and_instructions_default_to_none(self) -> None:
        version = _version()
        assert version.title is None
        assert version.instructions is None

    def test_none_title_and_instructions_are_permitted(self) -> None:
        version = _version(title=None, instructions=None)  # must not raise -- legacy-row state
        assert version.title is None
        assert version.instructions is None

    def test_a_real_title_and_instructions_construct_normally(self) -> None:
        version = _version(title="Tidy one surface", instructions="Pick a surface and clear it off.")
        assert version.title == "Tidy one surface"
        assert version.instructions == "Pick a surface and clear it off."

    def test_empty_string_title_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="title must not be empty or whitespace-only"):
            _version(title="", instructions="Do it.")

    def test_whitespace_only_title_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="title must not be empty or whitespace-only"):
            _version(title="   ", instructions="Do it.")

    def test_title_over_max_length_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="title must be at most 200 characters"):
            _version(title="x" * 201, instructions="Do it.")

    def test_title_at_max_length_is_accepted(self) -> None:
        version = _version(title="x" * 200, instructions="Do it.")
        assert len(version.title) == 200

    def test_empty_string_instructions_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="instructions must not be empty or whitespace-only"):
            _version(title="A title", instructions="")

    def test_whitespace_only_instructions_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="instructions must not be empty or whitespace-only"):
            _version(title="A title", instructions="   ")

    def test_instructions_over_max_length_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="instructions must be at most 2000 characters"):
            _version(title="A title", instructions="x" * 2001)

    def test_instructions_at_max_length_is_accepted(self) -> None:
        version = _version(title="A title", instructions="x" * 2000)
        assert len(version.instructions) == 2000


class TestTaskTemplateCatalogEntry:
    def test_is_not_frozen(self) -> None:
        """Deliberately mutable (TC-2) -- unlike TaskTemplateVersion,
        this is the current-state pointer, not append-only content."""
        entry = TaskTemplateCatalogEntry(
            template_id="tmpl-1", current_version=1,
            eligibility_status=TaskTemplateEligibilityStatus.ACTIVE, status_changed_at=FIXED_TIME,
        )
        entry.eligibility_status = TaskTemplateEligibilityStatus.DEACTIVATED  # must not raise
        assert entry.eligibility_status == TaskTemplateEligibilityStatus.DEACTIVATED

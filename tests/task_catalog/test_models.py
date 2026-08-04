"""tests/task_catalog/test_models.py"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import pytest

from task_catalog.models import (
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
        reflection_requirements=None, created_at=FIXED_TIME, created_via_consent_id="consent-1",
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

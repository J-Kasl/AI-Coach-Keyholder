"""tests/preference_profile/test_models.py"""

from __future__ import annotations

import dataclasses

import pytest

from preference_profile.models import (
    PreferenceProfileSnapshot,
    ProfileDisposition,
    ProfileEntry,
    ProfileOwnerKey,
    ProfileTopicId,
)


def _owner(value: str = "owner-1") -> ProfileOwnerKey:
    return ProfileOwnerKey(value=value)


def _topic(namespace: str = "provider_neutral", value: str = "topic-a") -> ProfileTopicId:
    return ProfileTopicId(namespace=namespace, value=value)


def _entry(*, id: str = "entry-1", owner: ProfileOwnerKey | None = None,
           topic: ProfileTopicId | None = None, disposition: ProfileDisposition = ProfileDisposition.PREFERENCE) -> ProfileEntry:
    return ProfileEntry(id=id, owner_key=owner or _owner(), topic=topic or _topic(), disposition=disposition)


class TestProfileOwnerKey:
    def test_valid_value_accepted(self) -> None:
        ProfileOwnerKey(value="owner-1")  # must not raise

    def test_empty_string_rejected(self) -> None:
        with pytest.raises(ValueError, match="ProfileOwnerKey.value"):
            ProfileOwnerKey(value="")

    def test_whitespace_only_rejected(self) -> None:
        with pytest.raises(ValueError, match="ProfileOwnerKey.value"):
            ProfileOwnerKey(value="   ")

    def test_non_string_rejected(self) -> None:
        with pytest.raises(ValueError, match="ProfileOwnerKey.value"):
            ProfileOwnerKey(value=123)  # type: ignore[arg-type]

    def test_immutable(self) -> None:
        owner = _owner()
        with pytest.raises(dataclasses.FrozenInstanceError):
            owner.value = "changed"  # type: ignore[misc]


class TestProfileTopicId:
    def test_valid_topic_accepted(self) -> None:
        ProfileTopicId(namespace="provider_neutral", value="topic-a")  # must not raise

    def test_empty_namespace_rejected(self) -> None:
        with pytest.raises(ValueError, match="ProfileTopicId.namespace"):
            ProfileTopicId(namespace="", value="topic-a")

    def test_whitespace_only_namespace_rejected(self) -> None:
        with pytest.raises(ValueError, match="ProfileTopicId.namespace"):
            ProfileTopicId(namespace="   ", value="topic-a")

    def test_non_string_namespace_rejected(self) -> None:
        with pytest.raises(ValueError, match="ProfileTopicId.namespace"):
            ProfileTopicId(namespace=1, value="topic-a")  # type: ignore[arg-type]

    def test_empty_value_rejected(self) -> None:
        with pytest.raises(ValueError, match="ProfileTopicId.value"):
            ProfileTopicId(namespace="provider_neutral", value="")

    def test_whitespace_only_value_rejected(self) -> None:
        with pytest.raises(ValueError, match="ProfileTopicId.value"):
            ProfileTopicId(namespace="provider_neutral", value="   ")

    def test_non_string_value_rejected(self) -> None:
        with pytest.raises(ValueError, match="ProfileTopicId.value"):
            ProfileTopicId(namespace="provider_neutral", value=1)  # type: ignore[arg-type]

    def test_equality_is_structural_and_case_sensitive(self) -> None:
        a = ProfileTopicId(namespace="provider_neutral", value="topic-a")
        b = ProfileTopicId(namespace="provider_neutral", value="topic-a")
        c = ProfileTopicId(namespace="Provider_Neutral", value="topic-a")
        assert a == b
        assert a != c

    def test_immutable(self) -> None:
        topic = _topic()
        with pytest.raises(dataclasses.FrozenInstanceError):
            topic.value = "changed"  # type: ignore[misc]


class TestProfileEntry:
    def test_valid_entry_accepted(self) -> None:
        _entry()  # must not raise

    def test_empty_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="ProfileEntry.id"):
            _entry(id="")

    def test_whitespace_only_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="ProfileEntry.id"):
            _entry(id="   ")

    def test_non_string_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="ProfileEntry.id"):
            ProfileEntry(id=123, owner_key=_owner(), topic=_topic(), disposition=ProfileDisposition.PREFERENCE)  # type: ignore[arg-type]

    def test_immutable(self) -> None:
        entry = _entry()
        with pytest.raises(dataclasses.FrozenInstanceError):
            entry.disposition = ProfileDisposition.HARD_LIMIT  # type: ignore[misc]

    def test_no_confirmation_status_or_related_fields_exist(self) -> None:
        """Structural proof, not just a docstring claim -- the field
        set is exactly these four, nothing import/consent/confirmation-shaped."""
        field_names = {f.name for f in dataclasses.fields(ProfileEntry)}
        assert field_names == {"id", "owner_key", "topic", "disposition"}


class TestPreferenceProfileSnapshot:
    def test_accepts_empty_tuple(self) -> None:
        snapshot = PreferenceProfileSnapshot(owner_key=_owner(), entries=())
        assert snapshot.entries == ()

    def test_accepts_several_different_topics_same_owner(self) -> None:
        owner = _owner()
        entries = (
            _entry(id="e1", owner=owner, topic=_topic(value="a")),
            _entry(id="e2", owner=owner, topic=_topic(value="b")),
        )
        snapshot = PreferenceProfileSnapshot(owner_key=owner, entries=entries)
        assert len(snapshot.entries) == 2

    def test_rejects_entry_belonging_to_a_different_owner(self) -> None:
        owner = _owner("owner-1")
        other_owner = _owner("owner-2")
        entries = (_entry(owner=other_owner),)
        with pytest.raises(ValueError, match="different owner"):
            PreferenceProfileSnapshot(owner_key=owner, entries=entries)

    def test_rejects_duplicate_topic(self) -> None:
        owner = _owner()
        topic = _topic()
        entries = (
            _entry(id="e1", owner=owner, topic=topic, disposition=ProfileDisposition.PREFERENCE),
            _entry(id="e2", owner=owner, topic=topic, disposition=ProfileDisposition.HARD_LIMIT),
        )
        with pytest.raises(ValueError, match="duplicate active topics"):
            PreferenceProfileSnapshot(owner_key=owner, entries=entries)

    def test_rejects_duplicate_topic_even_with_different_ids_and_dispositions(self) -> None:
        owner = _owner()
        topic = _topic()
        entries = (
            _entry(id="entry-alpha", owner=owner, topic=topic, disposition=ProfileDisposition.SOFT_LIMIT),
            _entry(id="entry-beta", owner=owner, topic=topic, disposition=ProfileDisposition.PREFERENCE),
        )
        with pytest.raises(ValueError, match="duplicate active topics"):
            PreferenceProfileSnapshot(owner_key=owner, entries=entries)

    def test_error_message_never_contains_owner_key_or_topic(self) -> None:
        owner = _owner("very-secret-owner-key")
        topic = _topic(value="very-secret-topic")
        entries = (
            _entry(id="e1", owner=owner, topic=topic),
            _entry(id="e2", owner=owner, topic=topic),
        )
        with pytest.raises(ValueError) as excinfo:
            PreferenceProfileSnapshot(owner_key=owner, entries=entries)
        assert "very-secret-owner-key" not in str(excinfo.value)
        assert "very-secret-topic" not in str(excinfo.value)

    def test_entries_must_be_a_tuple(self) -> None:
        with pytest.raises(ValueError, match="must be a tuple"):
            PreferenceProfileSnapshot(owner_key=_owner(), entries=[_entry()])  # type: ignore[arg-type]

    def test_immutable(self) -> None:
        snapshot = PreferenceProfileSnapshot(owner_key=_owner(), entries=())
        with pytest.raises(dataclasses.FrozenInstanceError):
            snapshot.entries = ()  # type: ignore[misc]


class TestNoImportOrConsentModelsExistInThisSlice:
    def test_models_module_defines_exactly_the_approved_public_surface(self) -> None:
        import preference_profile.models as models_module

        assert set(models_module.__all__) == {
            "ProfileOwnerKey", "ProfileTopicId", "ProfileDisposition",
            "ProfileEntry", "PreferenceProfileSnapshot", "TopicState",
        }

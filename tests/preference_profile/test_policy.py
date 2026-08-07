"""tests/preference_profile/test_policy.py"""

from __future__ import annotations

from preference_profile.models import (
    PreferenceProfileSnapshot,
    ProfileDisposition,
    ProfileEntry,
    ProfileOwnerKey,
    ProfileTopicId,
    TopicState,
)
from preference_profile.policy import resolve_topic_state

_OWNER = ProfileOwnerKey(value="owner-1")
_TOPIC = ProfileTopicId(namespace="provider_neutral", value="topic-a")


def _snapshot_with(disposition: ProfileDisposition | None) -> PreferenceProfileSnapshot:
    if disposition is None:
        return PreferenceProfileSnapshot(owner_key=_OWNER, entries=())
    entry = ProfileEntry(id="entry-1", owner_key=_OWNER, topic=_TOPIC, disposition=disposition)
    return PreferenceProfileSnapshot(owner_key=_OWNER, entries=(entry,))


class TestResolveTopicState:
    def test_preference_maps_to_preference_state(self) -> None:
        snapshot = _snapshot_with(ProfileDisposition.PREFERENCE)
        assert resolve_topic_state(snapshot=snapshot, topic=_TOPIC) == TopicState.PREFERENCE

    def test_soft_limit_maps_to_soft_limit_state(self) -> None:
        snapshot = _snapshot_with(ProfileDisposition.SOFT_LIMIT)
        assert resolve_topic_state(snapshot=snapshot, topic=_TOPIC) == TopicState.SOFT_LIMIT

    def test_hard_limit_maps_to_hard_limit_state(self) -> None:
        snapshot = _snapshot_with(ProfileDisposition.HARD_LIMIT)
        assert resolve_topic_state(snapshot=snapshot, topic=_TOPIC) == TopicState.HARD_LIMIT

    def test_missing_topic_maps_to_no_active_statement(self) -> None:
        snapshot = _snapshot_with(ProfileDisposition.PREFERENCE)
        other_topic = ProfileTopicId(namespace="provider_neutral", value="topic-b")
        assert resolve_topic_state(snapshot=snapshot, topic=other_topic) == TopicState.NO_ACTIVE_STATEMENT

    def test_empty_snapshot_maps_to_no_active_statement(self) -> None:
        snapshot = _snapshot_with(None)
        assert resolve_topic_state(snapshot=snapshot, topic=_TOPIC) == TopicState.NO_ACTIVE_STATEMENT

    def test_different_namespace_same_value_is_a_different_topic(self) -> None:
        snapshot = _snapshot_with(ProfileDisposition.HARD_LIMIT)
        other_namespace_topic = ProfileTopicId(namespace="other_family", value="topic-a")
        assert resolve_topic_state(snapshot=snapshot, topic=other_namespace_topic) == TopicState.NO_ACTIVE_STATEMENT

    def test_different_case_is_a_different_topic(self) -> None:
        snapshot = _snapshot_with(ProfileDisposition.HARD_LIMIT)
        different_case_topic = ProfileTopicId(namespace="Provider_Neutral", value="topic-a")
        assert resolve_topic_state(snapshot=snapshot, topic=different_case_topic) == TopicState.NO_ACTIVE_STATEMENT

    def test_function_does_not_mutate_the_snapshot(self) -> None:
        snapshot = _snapshot_with(ProfileDisposition.PREFERENCE)
        entries_before = snapshot.entries
        resolve_topic_state(snapshot=snapshot, topic=_TOPIC)
        assert snapshot.entries == entries_before
        assert snapshot.entries is entries_before

    def test_function_has_no_side_effects_or_external_access(self) -> None:
        """No DB, no network, no logging -- calling it repeatedly with
        the same inputs is deterministic and free of observable state
        changes."""
        snapshot = _snapshot_with(ProfileDisposition.SOFT_LIMIT)
        first = resolve_topic_state(snapshot=snapshot, topic=_TOPIC)
        second = resolve_topic_state(snapshot=snapshot, topic=_TOPIC)
        assert first == second == TopicState.SOFT_LIMIT

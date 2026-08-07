"""
preference_profile/policy.py

resolve_topic_state() -- pure, deterministic, no side effects, no
logging, no DB access, no import of any other subsystem. Because
PreferenceProfileSnapshot guarantees at most one entry per topic
(Cardinality Variant A, models.py's own construction-time invariant),
this function never performs conflict resolution or precedence over
multiple active values -- it only finds the single active entry (if
any) and maps its disposition to a TopicState.

Business precedence --

    hard limit > soft limit > no active statement > preference

-- remains a documented rule for a FUTURE update policy and future
eligibility policy (neither exists in this slice), not an algorithm
this function implements, since there is never more than one active
value to compare here.
"""

from __future__ import annotations

from preference_profile.models import PreferenceProfileSnapshot, ProfileTopicId, TopicState

__all__ = ["resolve_topic_state"]


def resolve_topic_state(*, snapshot: PreferenceProfileSnapshot, topic: ProfileTopicId) -> TopicState:
    for entry in snapshot.entries:
        if entry.topic == topic:
            return TopicState(entry.disposition.value)
    return TopicState.NO_ACTIVE_STATEMENT

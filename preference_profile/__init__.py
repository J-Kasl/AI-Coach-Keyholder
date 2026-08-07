"""
preference_profile -- Foundation Slice 1 only.

docs/architecture/preference_limits_profile_technical_design.md (draft,
not approved for implementation as a whole). See
preference_profile/README.md for the exact boundary of what this
package actually implements.
"""

from preference_profile.models import (
    PreferenceProfileSnapshot,
    ProfileDisposition,
    ProfileEntry,
    ProfileOwnerKey,
    ProfileTopicId,
    TopicState,
)
from preference_profile.policy import resolve_topic_state

__all__ = [
    "ProfileOwnerKey",
    "ProfileTopicId",
    "ProfileDisposition",
    "ProfileEntry",
    "PreferenceProfileSnapshot",
    "TopicState",
    "resolve_topic_state",
]

"""
preference_profile/models.py

docs/architecture/preference_limits_profile_technical_design.md (draft,
not approved for implementation as a whole). This module implements
ONLY the pure-domain Foundation Slice 1 -- see preference_profile/README.md
for the exact boundary. No repository, no revision workflow, no import
proposals, no consent, no eligibility integration, no external
providers, no runtime wiring of any kind exist here or anywhere in
this project yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "ProfileOwnerKey",
    "ProfileTopicId",
    "ProfileDisposition",
    "ProfileEntry",
    "PreferenceProfileSnapshot",
    "TopicState",
]


@dataclass(frozen=True, kw_only=True)
class ProfileOwnerKey:
    """Opaque -- this domain never imports UserAccount, Discord, or
    database types. A future composition/application layer may derive
    this value from UserAccount.id, but this module has no knowledge
    of that."""
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not self.value.strip():
            raise ValueError("ProfileOwnerKey.value must be a non-empty string.")


@dataclass(frozen=True, kw_only=True)
class ProfileTopicId:
    """
    `namespace` is a stable TAXONOMY FAMILY identifier, not a version
    number -- e.g. "provider_neutral", never "provider_neutral_v1"
    (that would silently conflate a family identifier with future
    versioning, which stays explicitly out of this slice's scope).
    Its only job here is preventing silent reinterpretation of an ID
    across incompatible schemes.

    Equality is structural (frozen dataclass default) and
    case-sensitive on both fields -- no normalization, no lowercasing,
    no trimming of the stored value (only emptiness is validated, via
    `.strip()`, which never mutates what is actually stored). Ordering
    is not defined -- no natural order exists between topics, so no
    `__lt__` is added. No taxonomy registry or migration mechanism
    exists in this slice.
    """
    namespace: str
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.namespace, str) or not self.namespace.strip():
            raise ValueError("ProfileTopicId.namespace must be a non-empty string.")
        if not isinstance(self.value, str) or not self.value.strip():
            raise ValueError("ProfileTopicId.value must be a non-empty string.")


class ProfileDisposition(StrEnum):
    PREFERENCE = "preference"
    SOFT_LIMIT = "soft_limit"
    HARD_LIMIT = "hard_limit"


@dataclass(frozen=True, kw_only=True)
class ProfileEntry:
    """
    No `confirmation_status`, `confirmed_at`, `supersedes_entry_id`,
    revision number, source/provider metadata, or consent metadata --
    deliberately. Existence of an entry in a PreferenceProfileSnapshot
    IS the confirmed, active state; there is no other state this type
    can represent. History, superseding, and optimistic concurrency
    belong to a future Slice 2, not this one.
    """
    id: str
    owner_key: ProfileOwnerKey
    topic: ProfileTopicId
    disposition: ProfileDisposition

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("ProfileEntry.id must be a non-empty string.")


@dataclass(frozen=True, kw_only=True)
class PreferenceProfileSnapshot:
    """
    At most one active entry per (owner_key, topic) -- enforced
    constructionally, not merely documented (Cardinality Variant A).
    Conflicting proposed changes are a future update-policy concern
    (Slice 2); this type can never represent more than one
    simultaneously active statement for the same topic.
    """
    owner_key: ProfileOwnerKey
    entries: tuple[ProfileEntry, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.entries, tuple):
            raise ValueError("PreferenceProfileSnapshot.entries must be a tuple.")

        for entry in self.entries:
            if entry.owner_key != self.owner_key:
                raise ValueError(
                    "PreferenceProfileSnapshot cannot contain an entry belonging to a different owner."
                )

        seen_topics: set[ProfileTopicId] = set()
        for entry in self.entries:
            if entry.topic in seen_topics:
                raise ValueError("PreferenceProfileSnapshot cannot contain duplicate active topics.")
            seen_topics.add(entry.topic)


class TopicState(StrEnum):
    HARD_LIMIT = "hard_limit"
    SOFT_LIMIT = "soft_limit"
    PREFERENCE = "preference"
    NO_ACTIVE_STATEMENT = "no_active_statement"

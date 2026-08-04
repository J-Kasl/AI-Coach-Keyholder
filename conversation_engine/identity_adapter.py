"""
conversation_engine/identity_adapter.py

Translates an already-selected identity_id into a runtime
CommunicationProfile -- a direct passthrough of ai/identity_catalog.py's
own existing data, never a duplicate representation. Does not select an
identity for the user (that remains onboarding's own job), does not
implement Behavioral Learning (ai_identity_technical_design.md ID-7/
ID-8, still fully unapproved), and never touches bootstrap catalog
values.
"""

from __future__ import annotations

from ai.identity_catalog import CommunicationProfile, get_identity
from conversation_engine.models import UnknownIdentityError

__all__ = ["build_identity_profile"]


def build_identity_profile(identity_id: str) -> CommunicationProfile:
    """Direct passthrough of the catalog's own immutable
    CommunicationProfile -- no new type, no copy, no field
    reinterpretation. Raises UnknownIdentityError deterministically
    for any identity_id the catalog doesn't recognize (get_identity()
    itself returns None rather than raising; this is the one place
    that turns that into a clear failure instead of letting a caller
    silently receive None)."""
    entry = get_identity(identity_id)
    if entry is None:
        raise UnknownIdentityError(f"No identity {identity_id!r} in the catalog.")
    return entry.communication_profile

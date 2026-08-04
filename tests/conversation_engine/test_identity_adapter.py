"""tests/conversation_engine/test_identity_adapter.py"""

from __future__ import annotations

import pytest

from ai.identity_catalog import get_identity
from conversation_engine.identity_adapter import build_identity_profile
from conversation_engine.models import UnknownIdentityError


class TestBuildIdentityProfile:
    def test_known_identity_returns_the_catalogs_own_profile_object(self) -> None:
        profile = build_identity_profile("sophia")
        catalog_entry = get_identity("sophia")
        assert profile is catalog_entry.communication_profile  # same object, no copy

    def test_unknown_identity_fails_deterministically(self) -> None:
        with pytest.raises(UnknownIdentityError, match="nonexistent-identity"):
            build_identity_profile("nonexistent-identity")

    def test_every_real_catalog_identity_resolves(self) -> None:
        from ai.identity_catalog import IDENTITY_CATALOG

        for entry in IDENTITY_CATALOG:
            profile = build_identity_profile(entry.identity_id)
            assert profile == entry.communication_profile

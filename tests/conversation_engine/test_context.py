"""tests/conversation_engine/test_context.py"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from ai.identity_catalog import CommunicationProfile
from conversation_engine.context import (
    ContextAssemblyOutcome,
    apply_situational_constraints,
    assemble_context,
    build_response_context,
)
from conversation_engine.models import (
    ConversationContextFragment,
    ProviderNamespaceCollisionError,
    ProviderNamespaceMismatchError,
    RequiredProviderFailedError,
    ResponseCategory,
    SituationalConstraints,
)

FIXED_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _profile(**overrides) -> CommunicationProfile:
    kwargs = dict(warmth=0.5, humor=0.5, teasing=0.5, assertiveness=0.5, formality=0.5, verbosity=0.5)
    kwargs.update(overrides)
    return CommunicationProfile(**kwargs)


class _OkProvider:
    def __init__(self, namespace: str) -> None:
        self.namespace = namespace

    def provide_context(self, *, now: datetime):
        return ConversationContextFragment(namespace=self.namespace, data={"ok": True})


class _NoneProvider:
    namespace = "empty"

    def provide_context(self, *, now: datetime):
        return None


class _RaisingProvider:
    namespace = "broken"

    def provide_context(self, *, now: datetime):
        raise RuntimeError("simulated provider failure")


class _MismatchedNamespaceProvider:
    namespace = "declared"

    def provide_context(self, *, now: datetime):
        return ConversationContextFragment(namespace="different", data={})


class TestAssembleContextHappyPath:
    def test_builds_a_snapshot_with_all_fragments(self) -> None:
        providers = [_OkProvider("a"), _OkProvider("b")]
        snapshot, outcomes = assemble_context(
            response_category=ResponseCategory.INFORMATIONAL_STATUS, current_user_message="hi",
            language="en", identity_profile=_profile(), situational_constraints=SituationalConstraints(),
            providers=providers, required_provider_namespaces=frozenset(), now=FIXED_TIME,
        )
        assert set(snapshot.context_fragments) == {"a", "b"}
        assert all(o.succeeded for o in outcomes)

    def test_empty_provider_list_produces_an_empty_but_valid_snapshot(self) -> None:
        snapshot, outcomes = assemble_context(
            response_category=ResponseCategory.ERROR_FALLBACK, current_user_message="hi",
            language="en", identity_profile=_profile(), situational_constraints=SituationalConstraints(),
            providers=[], required_provider_namespaces=frozenset(), now=FIXED_TIME,
        )
        assert dict(snapshot.context_fragments) == {}
        assert outcomes == ()


class TestOptionalProviderFailure:
    def test_none_returning_provider_does_not_block_assembly(self) -> None:
        snapshot, outcomes = assemble_context(
            response_category=ResponseCategory.INFORMATIONAL_STATUS, current_user_message="hi",
            language="en", identity_profile=_profile(), situational_constraints=SituationalConstraints(),
            providers=[_NoneProvider(), _OkProvider("a")], required_provider_namespaces=frozenset(), now=FIXED_TIME,
        )
        assert "empty" not in snapshot.context_fragments
        assert "a" in snapshot.context_fragments
        empty_outcome = next(o for o in outcomes if o.namespace == "empty")
        assert empty_outcome.succeeded is False

    def test_raising_provider_does_not_block_assembly(self) -> None:
        snapshot, outcomes = assemble_context(
            response_category=ResponseCategory.INFORMATIONAL_STATUS, current_user_message="hi",
            language="en", identity_profile=_profile(), situational_constraints=SituationalConstraints(),
            providers=[_RaisingProvider(), _OkProvider("a")], required_provider_namespaces=frozenset(), now=FIXED_TIME,
        )
        assert "broken" not in snapshot.context_fragments
        broken_outcome = next(o for o in outcomes if o.namespace == "broken")
        assert broken_outcome.succeeded is False
        assert "simulated provider failure" in broken_outcome.error


class TestRequiredProviderFailure:
    def test_required_namespace_missing_raises(self) -> None:
        with pytest.raises(RequiredProviderFailedError, match="broken"):
            assemble_context(
                response_category=ResponseCategory.GOVERNANCE_EXPLANATION, current_user_message="hi",
                language="en", identity_profile=_profile(), situational_constraints=SituationalConstraints(),
                providers=[_RaisingProvider()], required_provider_namespaces=frozenset({"broken"}), now=FIXED_TIME,
            )

    def test_never_proceeds_with_a_fabricated_fact_for_a_missing_required_namespace(self) -> None:
        """The exception itself IS the proof -- if this didn't raise,
        a caller could mistakenly treat the snapshot as complete."""
        with pytest.raises(RequiredProviderFailedError):
            assemble_context(
                response_category=ResponseCategory.GOVERNANCE_EXPLANATION, current_user_message="hi",
                language="en", identity_profile=_profile(), situational_constraints=SituationalConstraints(),
                providers=[], required_provider_namespaces=frozenset({"decision"}), now=FIXED_TIME,
            )


class TestNamespaceContract:
    def test_provider_returning_a_fragment_under_a_different_namespace_fails_deterministically(self) -> None:
        with pytest.raises(ProviderNamespaceMismatchError):
            assemble_context(
                response_category=ResponseCategory.INFORMATIONAL_STATUS, current_user_message="hi",
                language="en", identity_profile=_profile(), situational_constraints=SituationalConstraints(),
                providers=[_MismatchedNamespaceProvider()], required_provider_namespaces=frozenset(), now=FIXED_TIME,
            )

    def test_two_providers_claiming_the_same_namespace_fails_deterministically(self) -> None:
        with pytest.raises(ProviderNamespaceCollisionError):
            assemble_context(
                response_category=ResponseCategory.INFORMATIONAL_STATUS, current_user_message="hi",
                language="en", identity_profile=_profile(), situational_constraints=SituationalConstraints(),
                providers=[_OkProvider("dup"), _OkProvider("dup")], required_provider_namespaces=frozenset(),
                now=FIXED_TIME,
            )


class TestBuildResponseContextOrchestration:
    """THE single orchestration point -- point 4's own requirement that
    the API not leave ambiguous who turns a required-provider failure
    into a fallback."""

    def test_successful_assembly_returns_a_snapshot_outcome(self) -> None:
        outcome = build_response_context(
            response_category=ResponseCategory.INFORMATIONAL_STATUS, current_user_message="hi",
            language="en", identity_id="alex", situational_constraints=SituationalConstraints(),
            providers=[_OkProvider("a")], required_provider_namespaces=frozenset(), now=FIXED_TIME,
        )
        assert outcome.snapshot is not None
        assert outcome.fallback_response is None

    def test_required_provider_failure_returns_a_fallback_outcome_not_an_exception(self) -> None:
        outcome = build_response_context(
            response_category=ResponseCategory.GOVERNANCE_EXPLANATION, current_user_message="hi",
            language="en", identity_id="alex", situational_constraints=SituationalConstraints(),
            providers=[_RaisingProvider()], required_provider_namespaces=frozenset({"broken"}), now=FIXED_TIME,
        )
        assert outcome.snapshot is None
        assert outcome.fallback_response is not None
        assert outcome.fallback_response.response_category == ResponseCategory.ERROR_FALLBACK

    def test_outcome_never_sets_both_fields(self) -> None:
        with pytest.raises(ValueError, match="exactly one"):
            ContextAssemblyOutcome(snapshot=object(), fallback_response=object())  # type: ignore[arg-type]

    def test_outcome_never_sets_neither_field(self) -> None:
        with pytest.raises(ValueError, match="exactly one"):
            ContextAssemblyOutcome()


class TestApplySituationalConstraints:
    def test_no_constraints_returns_an_equal_but_new_profile(self) -> None:
        original = _profile(humor=0.8, teasing=0.7)
        result = apply_situational_constraints(original, SituationalConstraints())
        assert result == original

    def test_clamp_lowers_only_the_constrained_dimension(self) -> None:
        original = _profile(humor=0.8, teasing=0.7, assertiveness=0.9, verbosity=0.6, warmth=0.9, formality=0.4)
        result = apply_situational_constraints(original, SituationalConstraints(max_humor=0.3))
        assert result.humor == 0.3
        assert result.teasing == 0.7
        assert result.assertiveness == 0.9
        assert result.verbosity == 0.6
        assert result.warmth == 0.9
        assert result.formality == 0.4

    def test_clamp_above_current_value_has_no_effect(self) -> None:
        original = _profile(humor=0.3)
        result = apply_situational_constraints(original, SituationalConstraints(max_humor=0.9))
        assert result.humor == 0.3  # min(0.3, 0.9) == 0.3, unchanged

    def test_warmth_and_formality_are_never_clamped(self) -> None:
        original = _profile(warmth=0.95, formality=0.9)
        result = apply_situational_constraints(
            original,
            SituationalConstraints(max_humor=0.0, max_teasing=0.0, max_assertiveness=0.0, max_verbosity=0.0),
        )
        assert result.warmth == 0.95
        assert result.formality == 0.9

    def test_the_original_catalog_profile_object_is_structurally_unchanged(self) -> None:
        """Point 2's own explicit requirement -- the original stays
        bit-for-bit/structurally identical, not just 'a new object
        exists'."""
        from ai.identity_catalog import get_identity

        entry = get_identity("iris")
        original_profile = entry.communication_profile
        before = dataclasses_astuple(original_profile)

        apply_situational_constraints(original_profile, SituationalConstraints(max_humor=0.0, max_teasing=0.0))

        after = dataclasses_astuple(get_identity("iris").communication_profile)
        assert before == after
        assert get_identity("iris").communication_profile is original_profile  # still the same object


def dataclasses_astuple(profile: CommunicationProfile) -> tuple:
    return (profile.warmth, profile.humor, profile.teasing, profile.assertiveness, profile.formality, profile.verbosity)

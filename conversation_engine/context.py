"""
conversation_engine/context.py

Two distinct layers, deliberately not merged:

- `assemble_context()` -- the low-level primitive. Raises
  `RequiredProviderFailedError`/`ProviderNamespaceCollisionError`/
  `ProviderNamespaceMismatchError`. Never decides what to do about a
  required-provider failure -- it only reports it.
- `build_response_context()` -- the ONE orchestration point that
  decides what happens when a required provider fails: it catches
  `RequiredProviderFailedError` and returns a deterministic fallback
  response instead. No other code in this package independently makes
  that decision (per explicit review instruction: the API must not
  leave this ambiguous).

`apply_situational_constraints()` is a third, unrelated pure function
-- ResponseContextSnapshot carries the RAW catalog identity_profile and
situational_constraints as two separate fields (matching the design
document's own precedence table, Section 7); this function is a
utility a caller applies when it actually needs the effective, clamped
profile (Generation, in a later slice) -- context assembly itself
never bakes the clamp in silently.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from ai.identity_catalog import CommunicationProfile
from conversation_engine.fallback import FallbackReason, render_fallback
from conversation_engine.identity_adapter import build_identity_profile
from conversation_engine.models import (
    ConversationContextFragment,
    ConversationResponse,
    ProviderNamespaceCollisionError,
    ProviderNamespaceMismatchError,
    RequiredProviderFailedError,
    ResponseCategory,
    ResponseContextSnapshot,
    SituationalConstraints,
)
from conversation_engine.providers import ConversationContextProvider

__all__ = [
    "ProviderCallOutcome",
    "ContextAssemblyOutcome",
    "assemble_context",
    "build_response_context",
    "apply_situational_constraints",
]


@dataclass(frozen=True, kw_only=True)
class ProviderCallOutcome:
    """Point 7's own runtime diagnostic -- never persisted, no new
    audit table. One per provider actually called."""
    namespace: str
    succeeded: bool
    fragment: ConversationContextFragment | None = None
    error: str | None = None


def assemble_context(
    *, response_category: ResponseCategory, current_user_message: str, language: str,
    identity_profile: CommunicationProfile, situational_constraints: SituationalConstraints,
    providers: Sequence[ConversationContextProvider], required_provider_namespaces: frozenset[str],
    now: datetime,
) -> tuple[ResponseContextSnapshot, tuple[ProviderCallOutcome, ...]]:
    """
    CE-1/CE-5/CE-6/CE-8/CE-9/CE-10. Calls every provider in the given
    (static -- Slice 1 has no dynamic discovery) sequence, wrapping
    each call so a provider's own exception never propagates (the same
    wrap-and-never-propagate principle
    infrastructure/plugin_fault_boundary.py already established, sized
    down here -- no circuit breaker needed for a small static list in
    one process).

    A provider returning `None`, or raising, is an OPTIONAL failure by
    default -- recorded in the returned outcomes, the namespace simply
    absent from the snapshot. Only if that namespace is also listed in
    `required_provider_namespaces` does this raise
    `RequiredProviderFailedError` -- checked once, after every provider
    has run, never assumed mid-loop.

    A namespace mismatch (fragment.namespace != provider.namespace) or
    a namespace collision (two providers claiming the same namespace)
    is NOT a per-provider failure -- it is a deterministic assembly
    failure, raised immediately, distinct from an ordinary provider
    exception.
    """
    outcomes: list[ProviderCallOutcome] = []
    fragments: dict[str, ConversationContextFragment] = {}

    for provider in providers:
        try:
            fragment = provider.provide_context(now=now)
        except Exception as exc:  # noqa: BLE001 -- deliberately broad: this boundary's entire purpose
            outcomes.append(ProviderCallOutcome(namespace=provider.namespace, succeeded=False, error=str(exc)))
            continue

        if fragment is None:
            outcomes.append(
                ProviderCallOutcome(namespace=provider.namespace, succeeded=False, error="provider returned None")
            )
            continue

        if fragment.namespace != provider.namespace:
            raise ProviderNamespaceMismatchError(
                f"Provider {provider.namespace!r} returned a fragment with mismatched "
                f"namespace {fragment.namespace!r}."
            )
        if fragment.namespace in fragments:
            raise ProviderNamespaceCollisionError(
                f"Namespace {fragment.namespace!r} was already claimed by another provider "
                f"in this same context assembly."
            )

        fragments[fragment.namespace] = fragment
        outcomes.append(ProviderCallOutcome(namespace=provider.namespace, succeeded=True, fragment=fragment))

    missing_required = required_provider_namespaces - fragments.keys()
    if missing_required:
        raise RequiredProviderFailedError(
            f"Required provider namespace(s) unavailable: {sorted(missing_required)!r}"
        )

    snapshot = ResponseContextSnapshot(
        response_category=response_category, current_user_message=current_user_message,
        language=language, identity_profile=identity_profile,
        situational_constraints=situational_constraints, context_fragments=fragments,
    )
    return snapshot, tuple(outcomes)


@dataclass(frozen=True, kw_only=True)
class ContextAssemblyOutcome:
    """Exactly one of `snapshot`/`fallback_response` is set -- never
    both, never neither. The single, unambiguous result shape for
    `build_response_context()` -- a caller never has to guess which
    field is meaningful."""
    snapshot: ResponseContextSnapshot | None = None
    fallback_response: ConversationResponse | None = None
    provider_outcomes: tuple[ProviderCallOutcome, ...] = ()

    def __post_init__(self) -> None:
        if (self.snapshot is None) == (self.fallback_response is None):
            raise ValueError(
                "ContextAssemblyOutcome must set exactly one of snapshot/fallback_response, never both, never neither."
            )


def build_response_context(
    *, response_category: ResponseCategory, current_user_message: str, language: str,
    identity_id: str, situational_constraints: SituationalConstraints,
    providers: Sequence[ConversationContextProvider], required_provider_namespaces: frozenset[str],
    now: datetime,
) -> ContextAssemblyOutcome:
    """
    THE orchestration point (per explicit review instruction: the
    library API must not leave "who turns a required-provider failure
    into a fallback" ambiguous). Calls `assemble_context()`; if it
    raises `RequiredProviderFailedError`, returns the deterministic
    fallback response instead of propagating. No other function in
    this package makes this decision.

    `UnknownIdentityError` from `build_identity_profile()` is NOT
    caught here -- an unknown identity_id is a caller bug (a stored
    preference referencing a catalog entry that doesn't exist), not a
    "missing required context" situation this function's own fallback
    contract is meant to paper over.
    """
    identity_profile = build_identity_profile(identity_id)

    try:
        snapshot, outcomes = assemble_context(
            response_category=response_category, current_user_message=current_user_message,
            language=language, identity_profile=identity_profile,
            situational_constraints=situational_constraints, providers=providers,
            required_provider_namespaces=required_provider_namespaces, now=now,
        )
        return ContextAssemblyOutcome(snapshot=snapshot, provider_outcomes=outcomes)
    except RequiredProviderFailedError:
        return ContextAssemblyOutcome(
            fallback_response=render_fallback(FallbackReason.MISSING_REQUIRED_CONTEXT, language=language),
        )


def apply_situational_constraints(
    profile: CommunicationProfile, constraints: SituationalConstraints,
) -> CommunicationProfile:
    """
    Pure function, no detection logic of its own (the clamp is already
    decided by the caller -- ai_identity_technical_design.md Section
    6's own ID-4/ID-5). Returns a NEW CommunicationProfile; the
    catalog's own stored value is never mutated (it is a frozen
    dataclass already, but this function additionally never even
    attempts to touch it). Only the four dimensions
    `SituationalConstraints` actually names (Humor, Teasing,
    Assertiveness, Verbosity -- the design document's own Section 6
    list) are ever clamped, via `min()` only, never raised; Warmth and
    Formality are always passed through unchanged, since no
    `max_warmth`/`max_formality` field exists to clamp them with.
    """
    return CommunicationProfile(
        warmth=profile.warmth,
        humor=profile.humor if constraints.max_humor is None else min(profile.humor, constraints.max_humor),
        teasing=profile.teasing if constraints.max_teasing is None else min(profile.teasing, constraints.max_teasing),
        assertiveness=(
            profile.assertiveness if constraints.max_assertiveness is None
            else min(profile.assertiveness, constraints.max_assertiveness)
        ),
        formality=profile.formality,
        verbosity=profile.verbosity if constraints.max_verbosity is None else min(profile.verbosity, constraints.max_verbosity),
    )

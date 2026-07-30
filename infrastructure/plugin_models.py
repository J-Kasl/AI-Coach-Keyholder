"""
infrastructure/plugin_models.py

Data structures for the plugin infrastructure.
Canonical: docs/architecture/plugin_architecture_proposal.md v1.2.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

TrustTier = Literal["first_party", "third_party"]


class InvalidPluginManifestError(ValueError):
    """
    Raised when a PluginManifest violates one of its own structural
    rules (PLUG-2's namespace requirement, Decision 8's MVP scope).

    Only these two checks exist as of Step 1. `PluginRegistry` (Step 2)
    will very likely need broader manifest validation (version
    compatibility, malformed capability strings, ...) -- when that
    lands, this exception's docstring (and likely its error message
    shape) should be generalized to reflect that it covers manifest
    validation broadly, not only these two checks specifically.
    """


@dataclass(frozen=True, kw_only=True)
class PluginManifest:
    """
    Deliberately has NO `depends_on_plugins`/`optional_plugins`/
    `conflicts_with`/`load_after` field (Decision 9/PLUG-9,
    plugin_architecture_proposal.md v1.2) -- plugin-to-plugin
    dependencies are out of scope for the MVP. `PluginRegistry` (not
    yet built -- see that module's own README once it exists) loads
    every plugin independently, in a fixed deterministic order, and a
    plugin must never assume any other plugin exists or is loaded.
    """
    name: str
    version: str
    plugin_api_version: str
    min_core_version: str
    max_core_version: str | None = None
    trust_tier: TrustTier = "first_party"
    requested_read_capabilities: tuple[str, ...] = field(default_factory=tuple)
    consumes_event_types: tuple[str, ...] = field(default_factory=tuple)
    publishes_event_types: tuple[str, ...] = field(default_factory=tuple)
    registers_commands: tuple[str, ...] = field(default_factory=tuple)
    owns_tables: bool = False
    config_keys: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        # PLUG-2: every published event type must be namespaced under
        # this plugin's own name -- checked here, at manifest
        # construction, as the earliest possible point; also
        # re-checked at actual publish time by PluginSDK.publish_event()
        # (infrastructure/plugin_sdk.py), since a manifest passing this
        # check says nothing about what a plugin's code might try to
        # construct as an event_type string at runtime.
        expected_prefix = f"plugin_{self.name}."
        for event_type in self.publishes_event_types:
            if not event_type.startswith(expected_prefix):
                raise InvalidPluginManifestError(
                    f"Plugin {self.name!r} declares publishing {event_type!r}, "
                    f"which is outside its own namespace {expected_prefix!r} (PLUG-2)."
                )

        # Decision 8: only 'first_party' is a usable value in the MVP --
        # third-party plugin support is explicitly not designed yet.
        if self.trust_tier != "first_party":
            raise InvalidPluginManifestError(
                f"Plugin {self.name!r} declares trust_tier={self.trust_tier!r} -- "
                f"only 'first_party' is supported in the MVP (Decision 8)."
            )

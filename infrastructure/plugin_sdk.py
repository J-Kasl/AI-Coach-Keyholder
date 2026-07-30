"""
infrastructure/plugin_sdk.py

PluginSDK -- the ONLY surface a plugin may use to reach the rest of
this system (PLUG-4). Every instance is constructed fresh, per plugin,
by build_plugin_sdk(), and exposes only what that plugin's own
manifest actually declared (PLUG-5): an undeclared read capability is
not merely disallowed at call time -- the corresponding method is
never bound onto the instance at all, so `hasattr(sdk, 'get_goal')` is
False, not merely "calling it raises."

Canonical: docs/architecture/plugin_architecture_proposal.md v1.2,
Sections 3 (Decisions 1/2/4), 7 (PLUG-2/PLUG-3), 8 (PLUG-4/PLUG-5), 9.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from infrastructure.database import Database as CoreDatabase
from infrastructure.outbox import DomainEvent, write_event
from infrastructure.plugin_models import PluginManifest

if TYPE_CHECKING:
    from goal_management.repository import GoalManager
    from penalty_engine.repository import PenaltyEngine
    from recovery_plan.repository import RecoveryPlanManager
    from trust_manager.repository import TrustManager

__all__ = ["PluginCapabilityNotGrantedError", "PluginConfig", "PluginSDK", "build_plugin_sdk"]


class PluginCapabilityNotGrantedError(Exception):
    """
    Raised only defensively -- for read capabilities, PLUG-5's real
    guarantee is structural (the method is never bound on the instance
    at all; see build_plugin_sdk()), so this exception is not that
    mechanism's primary enforcement. It exists for the two cases where
    a plugin can construct a request at runtime that no static "is the
    method present" check could catch ahead of time: an event_type
    string built dynamically (publish_event(), PLUG-2) and a config key
    string built dynamically (PluginConfig.get(), Section 9).
    """


class PluginConfig:
    """
    `sdk.config` -- resolves only the plugin's own declared config
    keys (Section 9), the same "declare it or you can't reach it"
    discipline PLUG-5 applies to read capabilities. Namespacing
    (`PLUGIN_<NAME>_<KEY>` in the underlying `.env`-backed loader,
    `core/config.py`) is the caller's responsibility when building
    `values` -- this class only enforces that the plugin asked for
    this specific key in its own manifest.
    """

    def __init__(self, plugin_name: str, declared_keys: tuple[str, ...], values: dict[str, str]) -> None:
        self._plugin_name = plugin_name
        self._declared_keys = frozenset(declared_keys)
        self._values = values

    def get(self, key: str, default: str | None = None) -> str | None:
        if key not in self._declared_keys:
            raise PluginCapabilityNotGrantedError(
                f"Plugin {self._plugin_name!r} did not declare config key {key!r} in its manifest.config_keys."
            )
        return self._values.get(key, default)


class PluginSDK:
    """
    The curated, per-plugin surface (PLUG-4). Never constructed
    directly by a plugin or by anything other than build_plugin_sdk()
    -- see that function's own docstring for why. Read methods
    (`get_goal`, `get_domain_state`, ...) and `publish_event` are not
    declared on this class body at all; they are bound onto an
    instance dynamically by build_plugin_sdk(), one at a time, only for
    a granted capability.

    v1.2 (structural fix, found during review of Step 1): this class
    deliberately holds NO reference to the raw `Database`/`core`
    object at all, in any form -- not as `self._core`, not under any
    other name. An earlier draft stored `self._core = core` so
    `publish_event()` could open its own transaction; the problem is
    that Python's underscore-prefix convention is not real privacy --
    `sdk._core` was trivially reachable by any plugin code, handing it
    unrestricted `core.transaction()` access and silently defeating
    PLUG-1 and PLUG-5 regardless of what the plugin's manifest actually
    declared. `publish_event` is now bound onto each instance by
    build_plugin_sdk() as a closure over `core` -- `core` lives only in
    that closure's own cell, never as an attribute `dir(sdk)`,
    `sdk.__dict__`, or ordinary attribute access could surface. This
    raises the bar substantially (no accidental or casual discovery,
    no autocomplete suggesting it exists) but is not literally
    unbreakable: Python still allows inspecting a closure's captured
    cells directly (`sdk.publish_event.__closure__`). That gap is not
    this class's problem to solve -- first-party plugins (Decision 8)
    are trusted, reviewed code, and PLUG-4's automated import-boundary
    test is what actually catches a plugin deliberately reaching past
    its granted surface, the same way nothing stops a domain module
    from technically importing another one's internals either; genuine
    tamper-proof isolation is explicitly third-party's problem
    (sandboxing), deferred (Decision 8), not attempted here.
    """

    def __init__(self, *, manifest: PluginManifest, config: PluginConfig) -> None:
        self.manifest = manifest
        self.config = config


def build_plugin_sdk(
    manifest: PluginManifest, *, core: CoreDatabase, config_values: dict[str, str],
    trust_manager: "TrustManager | None" = None,
    penalty_engine: "PenaltyEngine | None" = None,
    recovery_plan: "RecoveryPlanManager | None" = None,
    goal_management: "GoalManager | None" = None,
) -> PluginSDK:
    """
    The ONLY place a PluginSDK is ever constructed (`PluginRegistry`,
    once built, is the only caller). Binds a read method onto the
    returned instance if, and only if, the manifest declared the
    corresponding capability -- this is PLUG-5's actual mechanism, not
    a convention documented elsewhere and hoped to be followed.

    Read capabilities delegate directly to a real domain module
    repository's own already-public methods (never a re-implementation
    of them) -- exactly the read set each module already exposes to
    every other consumer in this system, nothing narrower and nothing
    broader:

    - `trust_manager.read`: `get_domain_state`, `get_incident_assessment`,
      `get_confirmed_incidents_since`
    - `penalty_engine.read`: `get_active_or_frozen_penalty_window`,
      `get_authorization_freeze_state`, `get_penalty_window_relevant_domains`
    - `recovery_plan.read`: `get_recovery_task_completion`,
      `get_recovery_task`, `get_recovery_plan_for_window`, `get_recovery_plan`
    - `goal_management.read`: `get_goal`, `get_goal_version`,
      `get_change_proposal`, `get_change_proposal_content`

    Declaring a capability without supplying the corresponding
    repository is a caller error (`PluginRegistry`'s own bug, not a
    plugin author's) and raises immediately here, rather than building
    an SDK that would fail confusingly the first time a plugin actually
    calls the method.

    **Not yet built (flagged, not solved here):** a plugin with
    `manifest.owns_tables=True` has no way through this function to
    reach its own tables at all -- `build_plugin_sdk()` grants no
    database access beyond the narrow read methods above and
    `publish_event`. When `PluginRegistry` (Step 2) actually needs to
    give an `owns_tables=True` plugin a way to read/write its own
    tables, that should be a separate, narrow, plugin-scoped
    repository/database facade -- never the raw `core: CoreDatabase`
    this function itself receives, for exactly the reason `PluginSDK`'s
    own class docstring gives for why `core` is never stored as a
    reachable attribute in the first place.
    """
    sdk = PluginSDK(
        manifest=manifest,
        config=PluginConfig(manifest.name, manifest.config_keys, config_values),
    )

    # publish_event: bound as a closure over `core` (v1.2) -- `core`
    # lives only in this closure's own cell, never as an attribute the
    # returned `sdk` exposes; see PluginSDK's own docstring for why
    # this replaced an earlier `self._core = core` design.
    expected_prefix = f"plugin_{manifest.name}."
    declared_event_types = frozenset(manifest.publishes_event_types)

    def _publish_event_allowlist_check(event_type: str) -> None:
        """`publishes_event_types` is a binding allowlist, not merely a
        namespace hint -- the same "declare it or you can't reach it"
        discipline PLUG-5 already applies to read capabilities. A
        plugin whose manifest declares `publishes_event_types=('plugin_x.a',)`
        cannot publish `plugin_x.b` just because it shares the correct
        namespace prefix; every event_type actually published must be
        one of the specific values the plugin declared upfront."""
        if event_type not in declared_event_types:
            raise PluginCapabilityNotGrantedError(
                f"Plugin {manifest.name!r} did not declare {event_type!r} in its "
                f"manifest.publishes_event_types {tuple(declared_event_types)!r} (PLUG-2)."
            )
        # Defense in depth, and a clearer error for the specific case
        # of impersonating a domain module or another plugin: this can
        # only ever fire if PluginManifest.__post_init__'s own
        # namespace check was somehow bypassed (e.g. a manifest
        # mutated after construction), since __post_init__ already
        # guarantees every declared entry starts with this prefix.
        if not event_type.startswith(expected_prefix):
            raise PluginCapabilityNotGrantedError(
                f"Plugin {manifest.name!r} may only publish events under "
                f"{expected_prefix!r}; refused event_type={event_type!r} (PLUG-2)."
            )

    def _publish_event(event_type: str, payload: dict, *, now: datetime) -> None:
        """PLUG-2, re-checked here (not only at manifest-construction
        time in `PluginManifest.__post_init__`) -- a manifest passing
        that check says nothing about what a plugin's code might
        construct as an `event_type` string at actual call time.
        Checked against the full `publishes_event_types` allowlist, not
        only the namespace prefix -- see
        `_publish_event_allowlist_check()`'s own docstring.

        Opens its OWN transaction -- safe to call from a command
        handler (no enclosing transaction exists there) or any other
        context with no already-open `tx`. **Never call this from
        inside an event consumer handler** (it already runs inside
        `consume_event()`'s own transaction) -- use
        `publish_event_in_transaction(tx, ...)` there instead, or this
        will raise `NestedTransactionError`
        (`infrastructure/database.py`), the exact "Interpretation
        Handoff Pattern" violation
        `implementation_conventions.md` Section 3 already warns every
        module in this system about.
        """
        _publish_event_allowlist_check(event_type)
        with core.transaction() as tx:
            write_event(
                tx,
                DomainEvent(
                    event_type=event_type, source_module=f"plugin_{manifest.name}",
                    payload=payload, occurred_at=now,
                ),
            )

    def _publish_event_in_transaction(tx, event_type: str, payload: dict, *, now: datetime) -> None:
        """The event-consumer-handler counterpart to `publish_event()`
        -- takes an already-open `tx` (the same one
        `ConsumerRegistry`/`consume_event()` handed the handler) and
        never opens its own, matching the `_*_in_transaction` naming
        and shape every domain module in this system already uses for
        exactly this reason (e.g. `write_event(tx, ...)` itself). Same
        allowlist check as `publish_event()` -- see
        `_publish_event_allowlist_check()`."""
        _publish_event_allowlist_check(event_type)
        write_event(
            tx,
            DomainEvent(
                event_type=event_type, source_module=f"plugin_{manifest.name}",
                payload=payload, occurred_at=now,
            ),
        )

    sdk.publish_event = _publish_event
    sdk.publish_event_in_transaction = _publish_event_in_transaction

    capabilities = frozenset(manifest.requested_read_capabilities)

    if "trust_manager.read" in capabilities:
        if trust_manager is None:
            raise ValueError(f"Plugin {manifest.name!r} declares 'trust_manager.read' but no TrustManager was supplied.")
        sdk.get_domain_state = trust_manager.get_domain_state
        sdk.get_incident_assessment = trust_manager.get_incident_assessment
        sdk.get_confirmed_incidents_since = trust_manager.get_confirmed_incidents_since

    if "penalty_engine.read" in capabilities:
        if penalty_engine is None:
            raise ValueError(f"Plugin {manifest.name!r} declares 'penalty_engine.read' but no PenaltyEngine was supplied.")
        sdk.get_active_or_frozen_penalty_window = penalty_engine.get_active_or_frozen_penalty_window
        sdk.get_authorization_freeze_state = penalty_engine.get_authorization_freeze_state
        sdk.get_penalty_window_relevant_domains = penalty_engine.get_penalty_window_relevant_domains

    if "recovery_plan.read" in capabilities:
        if recovery_plan is None:
            raise ValueError(f"Plugin {manifest.name!r} declares 'recovery_plan.read' but no RecoveryPlanManager was supplied.")
        sdk.get_recovery_task_completion = recovery_plan.get_recovery_task_completion
        sdk.get_recovery_task = recovery_plan.get_recovery_task
        sdk.get_recovery_plan_for_window = recovery_plan.get_recovery_plan_for_window
        sdk.get_recovery_plan = recovery_plan.get_recovery_plan

    if "goal_management.read" in capabilities:
        if goal_management is None:
            raise ValueError(f"Plugin {manifest.name!r} declares 'goal_management.read' but no GoalManager was supplied.")
        sdk.get_goal = goal_management.get_goal
        sdk.get_goal_version = goal_management.get_goal_version
        sdk.get_change_proposal = goal_management.get_change_proposal
        sdk.get_change_proposal_content = goal_management.get_change_proposal_content

    return sdk

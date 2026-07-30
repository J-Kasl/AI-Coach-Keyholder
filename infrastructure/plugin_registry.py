"""
infrastructure/plugin_registry.py

PluginRegistry -- discovery, manifest validation, and wiring of
first-party plugins into the *existing* `ConsumerRegistry`/
`CommandRouter` (Step 2 of `plugin_architecture_proposal.md` v1.2
Section 27's own recommended order). No new event bus, no new command
router -- this module is a thin coordinator over infrastructure that
already exists (Step 1: `plugin_models.py`, `plugin_sdk.py`,
`plugin_fault_boundary.py`).

Deliberately out of scope for this module (per its own design
document, and per explicit review guidance before this Step began):
no startup integration, no plugin migrations, no real plugin. Proven
here entirely against synthetic, tmp_path-constructed plugin
directories in this module's own test suite
(tests/infrastructure/test_plugin_registry.py) -- the actual
`goal_celebration` example plugin (plugin_architecture_proposal.md
Section 20) is a later, separate step.

## The plugin directory convention this module expects

    plugins/<name>/
        __init__.py
        manifest.py     -- module-level `MANIFEST: PluginManifest`
        migrations/      -- only if manifest.owns_tables=True
            001_....sql   -- ends with its own seed INSERT into
                             plugin_schema_versions (012), the same
                             convention every core migration already
                             follows for schema_version (001)
        repository.py     -- only if manifest.owns_tables=True:
            def build_repository(core: CoreDatabase) -> Any
                ^ constructs and returns this plugin's OWN narrow
                  repository object. Unlike PluginSDK (which never
                  exposes raw `core`, Step 1's own fix), a plugin's own
                  repository.py IS given `core` directly -- see "Table
                  ownership and the trust boundary this implies" below
                  for why that is a deliberate, documented exception,
                  not an oversight.
        handlers.py       -- module-level, both optional depending on
                             what the manifest declares:
            def build_event_consumers(sdk: PluginSDK, repo: Any | None) -> dict[str, Callable[[Transaction, ClaimedDomainEvent], None]]
            def build_commands(sdk: PluginSDK, repo: Any | None) -> dict[str, tuple[str, Callable[[RequestContext], OutgoingMessage]]]
                                                          ^ (description, handler)
            `repo` is the object `repository.py`'s `build_repository()`
            returned, or `None` for a plugin with `owns_tables=False`.

This convention is this module's own invention, not specified at this
level of detail by `plugin_architecture_proposal.md` -- documented here
because it has to live somewhere, and flagged as exactly that (an
implementation detail, not an architectural commitment) so a future
revision is free to change it without that being an architecture
change.

## Table ownership and the trust boundary this implies

`build_plugin_sdk()` (Step 1) deliberately never exposes raw `core`
access -- that was the whole point of its own v1.2 fix. A plugin with
`manifest.owns_tables=True` genuinely needs *some* way to read/write
its own tables, though, and `PluginSDK`'s own docstring already flagged
this as "not yet built" pending a real need. This module resolves it:
such a plugin's own `repository.py` receives `core` directly, to
construct a repository object scoped (by the plugin author's own
code, not by anything this module can verify) to that plugin's own
tables. **This is a real, honestly-documented trust boundary, not a
loophole nobody noticed:** nothing in this module stops a careless or
malicious `owns_tables=True` plugin's `repository.py` from using that
`core` reference to touch a domain module's tables too -- PLUG-1's
enforcement for this specific path relies entirely on first-party
trust (code review), the same as it always has for anything a domain
module itself does with `core`. A capability-only plugin
(`owns_tables=False`) has no such exposure at all -- `PluginSDK` is
its only reachable surface, with `build_plugin_sdk()`'s full PLUG-5
guarantee intact.

## Why an event consumer's fault-boundary wrapper re-raises, but a
## command's does not

A plugin's event consumer handler runs *inside* `consume_event()`'s own
transaction (opened by `infrastructure.outbox`'s `apply_transition`).
If this module's wrapper swallowed a failure there (the way
`PluginFaultBoundary.call()` normally does, by design, for its
caller), `consume_event()`'s `write()` would carry on to
`mark_processed()` as if the handler had succeeded -- committing any
partial write the handler made before failing, and permanently marking
a genuinely failed delivery as done, blocking any future legitimate
retry. So the event-consumer wrapper here calls `PluginFaultBoundary`
for its tracking/circuit-breaker effect, but then deliberately
re-raises on failure, letting the real exception reach
`consume_event()`'s transaction boundary (rollback happens correctly,
`mark_processed()` never runs) before `ConsumerRegistry.dispatch()`'s
own per-registration exception boundary (added alongside this module,
see `infrastructure/consumer_registry.py`) catches it one level up.

A plugin's *command* handler has no such enclosing transaction to
protect -- any writes it makes (e.g. via `sdk.publish_event()`) are
already independently atomic. Its wrapper can safely swallow a failure
outright and return a generic, safe `OutgoingMessage` instead.
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from application.models import OutgoingMessage
from application.router import CommandRouter, RequestContext
from goal_management.repository import GoalManager
from infrastructure.consumer_registry import ConsumerRegistry
from infrastructure.database import Database as CoreDatabase
from infrastructure.database import Transaction
from infrastructure.outbox import ClaimedDomainEvent
from infrastructure.plugin_fault_boundary import PluginFaultBoundary
from infrastructure.plugin_migrations import apply_plugin_migrations
from infrastructure.plugin_models import InvalidPluginManifestError, PluginManifest
from infrastructure.plugin_sdk import PluginSDK, build_plugin_sdk
from penalty_engine.repository import PenaltyEngine
from recovery_plan.repository import RecoveryPlanManager
from trust_manager.repository import TrustManager

logger = logging.getLogger("ai_coach_keyholder.plugin_registry")

__all__ = ["CORE_VERSION", "LoadedPlugin", "PluginLoadFailure", "PluginRegistry"]

# BOOTSTRAP_DEFAULT(owner=undecided, mechanism=code):
# Temporary executable value pending an explicit ownership decision.
# How this should actually be maintained long-term (manual bump per
# release? tied to a git tag or package version?) is not decided --
# this is this slice's own placeholder, needed only so
# validate_compatibility() has something concrete to compare a
# plugin's declared min/max_core_version against.
CORE_VERSION = "1.0"


class PluginConventionError(Exception):
    """Raised when a plugin directory does not follow this module's own
    documented convention (missing manifest.py/MANIFEST, missing
    handlers.py, a handler declared in the manifest but not actually
    provided by handlers.py) -- distinct from InvalidPluginManifestError,
    which is about the manifest's own content being self-contradictory."""


@dataclass(frozen=True, kw_only=True)
class LoadedPlugin:
    """A plugin that discovery, validation, and loading all succeeded
    for -- its handlers are already registered into the shared
    ConsumerRegistry/CommandRouter by the time this is returned.
    `repo` is the object `repository.py`'s `build_repository()`
    returned, or `None` for an `owns_tables=False` plugin."""
    manifest: PluginManifest
    sdk: PluginSDK
    fault_boundary: PluginFaultBoundary
    repo: object = None


@dataclass(frozen=True, kw_only=True)
class PluginLoadFailure:
    """A plugin that did not load, at any stage (manifest missing/
    malformed, incompatible, handlers.py convention violated, or an
    exception during import/registration). `plugin_name` is the
    directory name when even the manifest could not be read."""
    plugin_name: str
    reason: str


class PluginRegistry:
    """
    One instance per process. Holds no plugin-specific state itself
    beyond what `load_all()` returns -- callers own the returned
    `LoadedPlugin`/`PluginLoadFailure` lists.
    """

    def __init__(
        self, *, plugins_dir: Path, core: CoreDatabase,
        consumer_registry: ConsumerRegistry, command_router: CommandRouter,
        trust_manager: TrustManager | None = None,
        penalty_engine: PenaltyEngine | None = None,
        recovery_plan: RecoveryPlanManager | None = None,
        goal_management: GoalManager | None = None,
        config_values_by_plugin: dict[str, dict[str, str]] | None = None,
        core_version: str = CORE_VERSION,
    ) -> None:
        self._plugins_dir = plugins_dir
        self._core = core
        self._consumer_registry = consumer_registry
        self._command_router = command_router
        self._trust_manager = trust_manager
        self._penalty_engine = penalty_engine
        self._recovery_plan = recovery_plan
        self._goal_management = goal_management
        self._config_values_by_plugin = config_values_by_plugin or {}
        self._core_version = core_version

    # -------------------------------------------------------------------
    # Discovery (PLUG-9: deterministic, no dependency resolution)
    # -------------------------------------------------------------------

    def discover(self) -> list[Path]:
        """Every immediate subdirectory of `plugins_dir` containing a
        `manifest.py`, sorted alphabetically by directory name -- fixed
        and deterministic (PLUG-9), never dependency-ordered."""
        if not self._plugins_dir.is_dir():
            return []
        candidates = [
            p for p in self._plugins_dir.iterdir()
            if p.is_dir() and (p / "manifest.py").is_file() and not p.name.startswith("_")
        ]
        return sorted(candidates, key=lambda p: p.name)

    # -------------------------------------------------------------------
    # Manifest loading and validation (before any plugin code beyond
    # manifest.py itself is ever imported -- Decision 6)
    # -------------------------------------------------------------------

    def load_manifest(self, plugin_dir: Path) -> PluginManifest:
        """Imports ONLY `<plugin_dir>/manifest.py` -- never
        `handlers.py` or anything else the plugin ships. Raises
        PluginConventionError/InvalidPluginManifestError; never returns
        a partially-valid manifest."""
        module_name = f"plugins.{plugin_dir.name}.manifest"
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:
            raise PluginConventionError(f"Could not import {module_name!r}: {exc}") from exc

        manifest = getattr(module, "MANIFEST", None)
        if manifest is None:
            raise PluginConventionError(f"{module_name!r} has no module-level MANIFEST attribute.")
        if not isinstance(manifest, PluginManifest):
            raise PluginConventionError(f"{module_name!r}.MANIFEST is not a PluginManifest instance.")
        if manifest.name != plugin_dir.name:
            raise PluginConventionError(
                f"Manifest name {manifest.name!r} does not match its directory name {plugin_dir.name!r}."
            )
        return manifest

    def validate_compatibility(self, manifest: PluginManifest) -> bool:
        """
        Decision 6: checked before the plugin's own implementation
        (handlers.py) is ever imported. `PluginManifest.__post_init__`
        already refuses `trust_tier != 'first_party'` at construction
        time (Decision 8) -- re-checked here too, defensively, since
        this is the method whose whole job is "is this plugin usable
        right now," not merely "was this manifest self-consistent when
        it was built."
        """
        if manifest.trust_tier != "first_party":
            return False
        if not _version_at_least(self._core_version, manifest.min_core_version):
            return False
        if manifest.max_core_version is not None and not _version_at_least(manifest.max_core_version, self._core_version):
            return False
        return True

    # -------------------------------------------------------------------
    # Loading: SDK construction, handler wiring, registration
    # -------------------------------------------------------------------

    def load(self, manifest: PluginManifest) -> LoadedPlugin:
        """
        Only ever called after validate_compatibility() has already
        returned True (load_all() enforces this ordering). Imports
        `<plugin>/handlers.py` -- the first point this plugin's actual
        implementation code runs (`<plugin>/repository.py`, if it owns
        tables, runs slightly earlier, immediately after its migrations
        are applied -- see this module's own docstring for why that
        file is trusted with `core` directly while `handlers.py` never
        is).
        """
        plugin_dir = self._plugins_dir / manifest.name

        repo = None
        if manifest.owns_tables:
            apply_plugin_migrations(self._core, manifest.name, plugin_dir / "migrations")
            repo_module_name = f"plugins.{manifest.name}.repository"
            try:
                repo_module = importlib.import_module(repo_module_name)
            except Exception as exc:
                raise PluginConventionError(f"Could not import {repo_module_name!r}: {exc}") from exc
            build_repository = getattr(repo_module, "build_repository", None)
            if build_repository is None:
                raise PluginConventionError(
                    f"Plugin {manifest.name!r} declares owns_tables=True but "
                    f"{repo_module_name!r} has no build_repository(core)."
                )
            repo = build_repository(self._core)

        sdk = build_plugin_sdk(
            manifest, core=self._core,
            config_values=self._config_values_by_plugin.get(manifest.name, {}),
            trust_manager=self._trust_manager, penalty_engine=self._penalty_engine,
            recovery_plan=self._recovery_plan, goal_management=self._goal_management,
        )
        fault_boundary = PluginFaultBoundary(manifest.name)

        handlers_module_name = f"plugins.{manifest.name}.handlers"
        try:
            handlers_module = importlib.import_module(handlers_module_name)
        except Exception as exc:
            raise PluginConventionError(f"Could not import {handlers_module_name!r}: {exc}") from exc

        event_consumers: dict[str, Callable] = {}
        if manifest.consumes_event_types:
            build_event_consumers = getattr(handlers_module, "build_event_consumers", None)
            if build_event_consumers is None:
                raise PluginConventionError(
                    f"Plugin {manifest.name!r} declares consumes_event_types but "
                    f"{handlers_module_name!r} has no build_event_consumers(sdk, repo)."
                )
            event_consumers = build_event_consumers(sdk, repo)
            missing = set(manifest.consumes_event_types) - set(event_consumers)
            if missing:
                raise PluginConventionError(
                    f"Plugin {manifest.name!r} declares consuming {sorted(missing)} but "
                    f"build_event_consumers() did not provide a handler for them."
                )

        commands: dict[str, tuple[str, Callable]] = {}
        if manifest.registers_commands:
            build_commands = getattr(handlers_module, "build_commands", None)
            if build_commands is None:
                raise PluginConventionError(
                    f"Plugin {manifest.name!r} declares registers_commands but "
                    f"{handlers_module_name!r} has no build_commands(sdk, repo)."
                )
            commands = build_commands(sdk, repo)
            missing_cmds = set(manifest.registers_commands) - set(commands)
            if missing_cmds:
                raise PluginConventionError(
                    f"Plugin {manifest.name!r} declares registering {sorted(missing_cmds)} but "
                    f"build_commands() did not provide a handler for them."
                )

        for event_type in manifest.consumes_event_types:
            real_handler = event_consumers[event_type]
            self._consumer_registry.register(
                event_type, f"plugin_{manifest.name}",
                _wrap_event_consumer(real_handler, fault_boundary, plugin_name=manifest.name),
            )

        for command in manifest.registers_commands:
            description, real_handler = commands[command]
            self._command_router.register(
                command, description,
                _wrap_command_handler(real_handler, fault_boundary, plugin_name=manifest.name),
            )

        return LoadedPlugin(manifest=manifest, sdk=sdk, fault_boundary=fault_boundary, repo=repo)

    # -------------------------------------------------------------------
    # The main entry point
    # -------------------------------------------------------------------

    def load_all(self) -> tuple[list[LoadedPlugin], list[PluginLoadFailure]]:
        """
        Discovers, validates, and loads every plugin under
        `plugins_dir`, in deterministic order (PLUG-9). One plugin
        failing at any stage never prevents the others from loading --
        each stage's failure is caught individually and recorded as a
        `PluginLoadFailure`, never raised out of this method.
        """
        loaded: list[LoadedPlugin] = []
        failures: list[PluginLoadFailure] = []

        for plugin_dir in self.discover():
            try:
                manifest = self.load_manifest(plugin_dir)
            except (PluginConventionError, InvalidPluginManifestError) as exc:
                logger.error("Plugin at %s failed to load its manifest: %s", plugin_dir, exc)
                failures.append(PluginLoadFailure(plugin_name=plugin_dir.name, reason=str(exc)))
                continue

            if not self.validate_compatibility(manifest):
                reason = (
                    f"incompatible: trust_tier={manifest.trust_tier!r}, "
                    f"min_core_version={manifest.min_core_version!r}, "
                    f"max_core_version={manifest.max_core_version!r}, "
                    f"running core_version={self._core_version!r}"
                )
                logger.error("Plugin %r is incompatible, skipped: %s", manifest.name, reason)
                failures.append(PluginLoadFailure(plugin_name=manifest.name, reason=reason))
                continue

            try:
                loaded.append(self.load(manifest))
            except Exception as exc:
                logger.exception("Plugin %r failed to load.", manifest.name)
                failures.append(PluginLoadFailure(plugin_name=manifest.name, reason=str(exc)))
                continue

        return loaded, failures


def _wrap_event_consumer(
    real_handler: Callable[[Transaction, ClaimedDomainEvent], None],
    fault_boundary: PluginFaultBoundary, *, plugin_name: str,
) -> Callable[[Transaction, ClaimedDomainEvent], None]:
    """See this module's own docstring for why this wrapper re-raises
    on failure instead of swallowing it the way PluginFaultBoundary
    normally would for its caller."""

    def wrapped(tx: Transaction, event: ClaimedDomainEvent) -> None:
        # `now` for the fault boundary's own failure-window tracking is
        # the event's own occurred_at, not a fresh system-time read --
        # this handler has no injected Clock, and this project's own
        # guard test forbids calling datetime.now()/utcnow() outside
        # infrastructure/clock.py.
        result = fault_boundary.call(
            lambda: real_handler(tx, event),
            context=f"event_type={event.event_type}", now=event.occurred_at,
        )
        if not result.succeeded:
            raise RuntimeError(f"plugin {plugin_name!r} event consumer failed: {result.error}")

    return wrapped


def _wrap_command_handler(
    real_handler: Callable[[RequestContext], OutgoingMessage],
    fault_boundary: PluginFaultBoundary, *, plugin_name: str,
) -> Callable[[RequestContext], OutgoingMessage]:
    """No enclosing transaction to protect here (see this module's own
    docstring) -- safe to swallow a failure outright and return a
    generic, safe reply instead."""

    def wrapped(ctx: RequestContext) -> OutgoingMessage:
        result = fault_boundary.call(lambda: real_handler(ctx), context=f"command (plugin={plugin_name})", now=ctx.now)
        if not result.succeeded:
            return OutgoingMessage(text="Something went wrong handling that. It's been logged.")
        return result.value

    return wrapped


def _version_at_least(actual: str, required: str) -> bool:
    """Minimal dotted-integer version comparison (e.g. '1.2' >= '1.0')
    -- deliberately not a full semver implementation or a new
    dependency; sufficient for this slice's own `min_core_version`/
    `max_core_version` checks."""
    def parse(v: str) -> tuple[int, ...]:
        return tuple(int(part) for part in v.split("."))
    return parse(actual) >= parse(required)

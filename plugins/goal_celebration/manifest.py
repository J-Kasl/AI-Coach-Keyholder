"""
plugins/goal_celebration/manifest.py

The first real plugin (plugin_architecture_proposal.md Section 20) --
proves the whole design end-to-end against a genuine, already-published
event (`goal.completed`, `goal_management`) rather than a hypothetical
one. See handlers.py/repository.py for what it actually does.
"""

from infrastructure.plugin_models import PluginManifest

MANIFEST = PluginManifest(
    name="goal_celebration",
    version="0.1.0",
    plugin_api_version="1.0",
    min_core_version="1.0",
    requested_read_capabilities=("goal_management.read",),
    consumes_event_types=("goal.completed",),
    publishes_event_types=("plugin_goal_celebration.sent",),
    owns_tables=True,
    config_keys=(),
)

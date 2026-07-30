"""tests/infrastructure/test_plugin_models.py"""

from __future__ import annotations

import pytest

from infrastructure.plugin_models import InvalidPluginManifestError, PluginManifest


def _base_kwargs(**overrides) -> dict:
    kwargs = dict(
        name="goal_celebration", version="0.1.0", plugin_api_version="1.0",
        min_core_version="1.0",
    )
    kwargs.update(overrides)
    return kwargs


class TestPluginManifestDefaults:
    def test_minimal_manifest_constructs(self) -> None:
        manifest = PluginManifest(**_base_kwargs())
        assert manifest.trust_tier == "first_party"
        assert manifest.requested_read_capabilities == ()
        assert manifest.owns_tables is False

    def test_has_no_dependency_fields(self) -> None:
        """PLUG-9 / Decision 9: dependency-expressing fields do not
        exist on this dataclass at all in the MVP."""
        import dataclasses
        field_names = {f.name for f in dataclasses.fields(PluginManifest)}
        assert "depends_on_plugins" not in field_names
        assert "optional_plugins" not in field_names
        assert "conflicts_with" not in field_names
        assert "load_after" not in field_names


class TestPlug2NamespaceValidation:
    def test_correctly_namespaced_event_is_accepted(self) -> None:
        manifest = PluginManifest(**_base_kwargs(publishes_event_types=("plugin_goal_celebration.sent",)))
        assert manifest.publishes_event_types == ("plugin_goal_celebration.sent",)

    def test_impersonating_a_domain_module_event_is_rejected(self) -> None:
        with pytest.raises(InvalidPluginManifestError):
            PluginManifest(**_base_kwargs(publishes_event_types=("goal.completed",)))

    def test_impersonating_another_plugins_namespace_is_rejected(self) -> None:
        with pytest.raises(InvalidPluginManifestError):
            PluginManifest(**_base_kwargs(publishes_event_types=("plugin_other_plugin.sent",)))


class TestDecision8TrustTier:
    def test_first_party_is_accepted(self) -> None:
        manifest = PluginManifest(**_base_kwargs(trust_tier="first_party"))
        assert manifest.trust_tier == "first_party"

    def test_third_party_is_rejected_in_the_mvp(self) -> None:
        with pytest.raises(InvalidPluginManifestError):
            PluginManifest(**_base_kwargs(trust_tier="third_party"))

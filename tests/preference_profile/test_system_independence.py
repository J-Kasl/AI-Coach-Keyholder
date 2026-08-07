"""
tests/preference_profile/test_system_independence.py

Static AST checks -- preference_profile/ must not import application,
bot, conversation_engine, memory_system, infrastructure.database,
task_catalog, or discord -- and no other package in this project may
import preference_profile yet (zero runtime wiring exists in this
slice).
"""

from __future__ import annotations

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
PREFERENCE_PROFILE_DIR = PROJECT_ROOT / "preference_profile"

_FORBIDDEN_MODULE_PREFIXES = (
    "application",
    "bot",
    "conversation_engine",
    "memory_system",
    "infrastructure.database",
    "database",
    "task_catalog",
    "discord",
)


def _imported_module_names(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


class TestNoForbiddenImports:
    def test_preference_profile_has_no_forbidden_imports(self) -> None:
        offending: list[str] = []
        for py_file in PREFERENCE_PROFILE_DIR.rglob("*.py"):
            for name in _imported_module_names(py_file):
                if any(name == prefix or name.startswith(prefix + ".") for prefix in _FORBIDDEN_MODULE_PREFIXES):
                    offending.append(f"{py_file.relative_to(PROJECT_ROOT)}: {name}")
        assert offending == [], f"Found forbidden imports: {offending}"

    def test_preference_profile_only_imports_standard_library_and_itself(self) -> None:
        """Slice 1 has zero third-party or cross-package dependencies
        at all -- only stdlib (dataclasses, enum, __future__) and its
        own submodules."""
        allowed_prefixes = ("__future__", "dataclasses", "enum", "typing", "preference_profile")
        offending: list[str] = []
        for py_file in PREFERENCE_PROFILE_DIR.rglob("*.py"):
            for name in _imported_module_names(py_file):
                if not any(name == prefix or name.startswith(prefix + ".") for prefix in allowed_prefixes):
                    offending.append(f"{py_file.relative_to(PROJECT_ROOT)}: {name}")
        assert offending == []


class TestNoOtherPackageImportsPreferenceProfileYet:
    def test_no_existing_package_references_preference_profile(self) -> None:
        """Zero runtime wiring exists in this slice -- confirmed, not
        just claimed."""
        checked_packages = [
            "application", "bot", "conversation_engine", "memory_system",
            "trust_manager", "penalty_engine", "recovery_plan",
            "goal_management", "task_catalog", "advanced_mode", "infrastructure", "ai",
        ]
        offending: list[str] = []
        for package in checked_packages:
            package_dir = PROJECT_ROOT / package
            if not package_dir.is_dir():
                continue
            for py_file in package_dir.rglob("*.py"):
                names = _imported_module_names(py_file)
                if any(n == "preference_profile" or n.startswith("preference_profile.") for n in names):
                    offending.append(str(py_file.relative_to(PROJECT_ROOT)))
        assert offending == []

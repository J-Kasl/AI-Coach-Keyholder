"""
tests/lock_state/test_system_independence.py

Static AST checks -- lock_state/ must not import Discord,
conversation_engine, memory_system, preference_profile, or any bot
runtime -- and no such module may import lock_state yet either (this
slice implements no wiring at all beyond the domain module and its own
repository).
"""

from __future__ import annotations

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
LOCK_STATE_DIR = PROJECT_ROOT / "lock_state"

_FORBIDDEN_MODULE_PREFIXES = (
    "discord",
    "bot",
    "conversation_engine",
    "memory_system",
    "preference_profile",
    "application",
    "task_catalog",
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
    def test_lock_state_has_no_forbidden_imports(self) -> None:
        offending: list[str] = []
        for py_file in LOCK_STATE_DIR.rglob("*.py"):
            for name in _imported_module_names(py_file):
                if any(name == prefix or name.startswith(prefix + ".") for prefix in _FORBIDDEN_MODULE_PREFIXES):
                    offending.append(f"{py_file.relative_to(PROJECT_ROOT)}: {name}")
        assert offending == [], f"Found forbidden imports: {offending}"

    def test_lock_state_only_imports_stdlib_and_established_infrastructure(self) -> None:
        """The only cross-package dependency this slice has is the
        existing infrastructure.database module -- the same shared
        transaction/apply_transition helper every other governed-write
        module already uses."""
        allowed_prefixes = ("__future__", "dataclasses", "datetime", "enum", "pathlib", "uuid", "infrastructure", "lock_state")
        offending: list[str] = []
        for py_file in LOCK_STATE_DIR.rglob("*.py"):
            for name in _imported_module_names(py_file):
                if not any(name == prefix or name.startswith(prefix + ".") for prefix in allowed_prefixes):
                    offending.append(f"{py_file.relative_to(PROJECT_ROOT)}: {name}")
        assert offending == []


class TestNoOtherPackageImportsLockStateYet:
    def test_no_existing_package_references_lock_state(self) -> None:
        checked_packages = [
            "application", "bot", "conversation_engine", "memory_system", "preference_profile",
            "trust_manager", "penalty_engine", "recovery_plan", "goal_management",
            "task_catalog", "advanced_mode", "infrastructure", "ai",
        ]
        offending: list[str] = []
        for package in checked_packages:
            package_dir = PROJECT_ROOT / package
            if not package_dir.is_dir():
                continue
            for py_file in package_dir.rglob("*.py"):
                names = _imported_module_names(py_file)
                if any(n == "lock_state" or n.startswith("lock_state.") for n in names):
                    offending.append(str(py_file.relative_to(PROJECT_ROOT)))
        assert offending == []

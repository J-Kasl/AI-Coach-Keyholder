"""
tests/task_runtime/test_system_independence.py

Static AST checks -- task_runtime -> task_catalog and
task_runtime -> lock_state are the ONLY permitted directions.
task_catalog must NEVER import task_runtime (no cyclic dependency).
No conversation_engine/memory_system/preference_profile/bot/Discord
import anywhere in task_runtime.
"""

from __future__ import annotations

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
TASK_RUNTIME_DIR = PROJECT_ROOT / "task_runtime"
TASK_CATALOG_DIR = PROJECT_ROOT / "task_catalog"

_FORBIDDEN_FOR_TASK_RUNTIME = (
    "discord", "bot", "conversation_engine", "memory_system", "preference_profile", "application",
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


class TestTaskRuntimeForbiddenImports:
    def test_task_runtime_has_no_forbidden_imports(self) -> None:
        offending: list[str] = []
        for py_file in TASK_RUNTIME_DIR.rglob("*.py"):
            for name in _imported_module_names(py_file):
                if any(name == prefix or name.startswith(prefix + ".") for prefix in _FORBIDDEN_FOR_TASK_RUNTIME):
                    offending.append(f"{py_file.relative_to(PROJECT_ROOT)}: {name}")
        assert offending == [], f"Found forbidden imports: {offending}"


class TestNoCyclicDependency:
    def test_task_catalog_never_imports_task_runtime(self) -> None:
        """The direction is one-way: task_runtime -> task_catalog,
        never the reverse. LockRequirement lives in task_catalog
        precisely so this direction never needs to be violated."""
        offending: list[str] = []
        for py_file in TASK_CATALOG_DIR.rglob("*.py"):
            names = _imported_module_names(py_file)
            if any(n == "task_runtime" or n.startswith("task_runtime.") for n in names):
                offending.append(str(py_file.relative_to(PROJECT_ROOT)))
        assert offending == []

    def test_task_runtime_does_import_task_catalog_and_lock_state(self) -> None:
        """Positive proof the approved dependency direction actually exists."""
        all_names: list[str] = []
        for py_file in TASK_RUNTIME_DIR.rglob("*.py"):
            all_names.extend(_imported_module_names(py_file))
        assert any(n == "task_catalog" or n.startswith("task_catalog.") for n in all_names)
        assert any(n == "lock_state" or n.startswith("lock_state.") for n in all_names)


class TestApplicationServiceIsTheOnlyApprovedImporter:
    """application/service.py (First Testable Keyholder Milestone,
    Slice C) is the ONE approved integration point for task_runtime --
    constructed the same way advanced_mode is, directly inside
    ApplicationService.__init__, not via DI from bot/discord_bot.py's
    own composition root. No other file anywhere may import task_runtime."""

    def test_no_other_file_references_task_runtime(self) -> None:
        checked_packages = [
            "application", "bot", "conversation_engine", "memory_system", "preference_profile",
            "trust_manager", "penalty_engine", "recovery_plan", "goal_management",
            "advanced_mode", "infrastructure", "ai", "lock_state",
        ]
        allowed_application_files = {"service.py"}
        offending: list[str] = []
        for package in checked_packages:
            package_dir = PROJECT_ROOT / package
            if not package_dir.is_dir():
                continue
            for py_file in package_dir.rglob("*.py"):
                if package == "application" and py_file.name in allowed_application_files:
                    continue
                names = _imported_module_names(py_file)
                if any(n == "task_runtime" or n.startswith("task_runtime.") for n in names):
                    offending.append(str(py_file.relative_to(PROJECT_ROOT)))
        assert offending == []

    def test_service_py_does_in_fact_import_task_runtime(self) -> None:
        """Positive proof the approved integration actually exists."""
        service_py = PROJECT_ROOT / "application" / "service.py"
        names = _imported_module_names(service_py)
        assert any(n == "task_runtime" or n.startswith("task_runtime.") for n in names)

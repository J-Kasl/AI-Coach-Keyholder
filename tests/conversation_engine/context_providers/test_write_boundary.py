"""
tests/conversation_engine/context_providers/test_write_boundary.py

Slice D's own structural write-boundary guard: no file in
conversation_engine/context_providers/ may import
LockStateAdministration/TaskRuntimeAdministration/
TaskCatalogAdministration/AdvancedModeAdministration. Verified by AST,
not by convention.
"""

from __future__ import annotations

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
CONTEXT_PROVIDERS_DIR = PROJECT_ROOT / "conversation_engine" / "context_providers"

_FORBIDDEN_NAMES = (
    "LockStateAdministration",
    "TaskRuntimeAdministration",
    "TaskCatalogAdministration",
    "AdvancedModeAdministration",
)


def _imported_names(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
    return names


class TestNoAdministrationImportsAnywhereInContextProviders:
    def test_no_forbidden_administration_class_is_ever_imported(self) -> None:
        offending: list[str] = []
        for py_file in CONTEXT_PROVIDERS_DIR.rglob("*.py"):
            names = _imported_names(py_file)
            for forbidden in _FORBIDDEN_NAMES:
                if forbidden in names:
                    offending.append(f"{py_file.relative_to(PROJECT_ROOT)}: {forbidden}")
        assert offending == [], f"Found forbidden Administration imports: {offending}"

    def test_lock_state_provider_only_imports_lock_state_read_type(self) -> None:
        """Positive proof: LockStateContextProvider imports LockState
        (read-only), never LockStateAdministration."""
        path = CONTEXT_PROVIDERS_DIR / "lock_state_provider.py"
        names = _imported_names(path)
        assert "LockState" in names
        assert "LockStateAdministration" not in names

    def test_active_task_provider_only_imports_read_types(self) -> None:
        path = CONTEXT_PROVIDERS_DIR / "active_task_provider.py"
        names = _imported_names(path)
        assert "TaskRuntime" in names
        assert "TaskCatalog" in names
        assert "TaskRuntimeAdministration" not in names
        assert "TaskCatalogAdministration" not in names

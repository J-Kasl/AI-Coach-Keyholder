"""
tests/memory_system/test_system_independence.py

Static AST checks -- memory_system/ must not import sqlite3/the DB
layer, Discord, or conversation_engine (this slice does not wire
Conversation Engine to memory_system at all -- that is Conversation
Engine Slice 3's own future work).
"""

from __future__ import annotations

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
MEMORY_SYSTEM_DIR = PROJECT_ROOT / "memory_system"

_FORBIDDEN_MODULE_PREFIXES = (
    "sqlite3",
    "infrastructure.database",
    "database",
    "discord",
    "bot",
    "conversation_engine",
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
    def test_memory_system_has_no_db_discord_or_conversation_engine_imports(self) -> None:
        offending: list[str] = []
        for py_file in MEMORY_SYSTEM_DIR.rglob("*.py"):
            for name in _imported_module_names(py_file):
                if any(name == prefix or name.startswith(prefix + ".") for prefix in _FORBIDDEN_MODULE_PREFIXES):
                    offending.append(f"{py_file.relative_to(PROJECT_ROOT)}: {name}")
        assert offending == [], f"Found forbidden imports: {offending}"

    def test_no_other_package_imports_memory_system_yet(self) -> None:
        """This slice is standalone -- nothing in the existing codebase
        (application/, bot/, conversation_engine/, any domain module)
        references memory_system yet. Wiring it in is explicitly out of
        scope (Conversation Engine Slice 3's own future work)."""
        checked_packages = [
            "application", "bot", "conversation_engine", "trust_manager",
            "penalty_engine", "recovery_plan", "goal_management",
            "task_catalog", "advanced_mode", "infrastructure", "ai",
        ]
        offending: list[str] = []
        for package in checked_packages:
            package_dir = PROJECT_ROOT / package
            if not package_dir.is_dir():
                continue
            for py_file in package_dir.rglob("*.py"):
                names = _imported_module_names(py_file)
                if any(n == "memory_system" or n.startswith("memory_system.") for n in names):
                    offending.append(str(py_file.relative_to(PROJECT_ROOT)))
        assert offending == []


class TestNoDomainOrGovernanceSideEffects:
    def test_working_memory_module_touches_no_database_at_all(self) -> None:
        """Structural proof, not behavioral -- there is no database
        handle anywhere in this module for a side effect to even reach."""
        working_memory_py = MEMORY_SYSTEM_DIR / "working_memory.py"
        source = working_memory_py.read_text(encoding="utf-8")
        assert "sqlite3" not in source
        assert "transaction" not in source.lower()


class TestNoRawContentLogging:
    def test_no_logging_import_anywhere_in_the_package(self) -> None:
        """This slice's own contract: the module does not log at all --
        checked structurally (no `logging` import exists to misuse),
        not just "no log line happens to mention content today"."""
        offending: list[str] = []
        for py_file in MEMORY_SYSTEM_DIR.rglob("*.py"):
            if "logging" in _imported_module_names(py_file):
                offending.append(str(py_file.relative_to(PROJECT_ROOT)))
        assert offending == []

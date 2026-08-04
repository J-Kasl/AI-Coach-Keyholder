"""
tests/conversation_engine/test_system_independence.py

CE-20. Per explicit review instruction: does NOT run the existing
pytest suite from inside a pytest test (that's the final, separate
regression-suite step of the implementation, not something this file
does). Instead:

1. Statically parses every domain module / application / bot source
   file's own AST and confirms none of them import `conversation_engine`.
2. Constructs a real ApplicationService the ordinary way and directly
   exercises a few existing command paths, proving their behavior is
   unaffected by conversation_engine's mere existence on disk.
"""

from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

import pytest

from application.models import IncomingMessage
from application.service import ApplicationService
from infrastructure.database import Database as CoreDatabase

FIXED_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)
PROJECT_ROOT = Path(__file__).parent.parent.parent

# Every first-party package this project's own domain/application/bot
# layer consists of, excluding conversation_engine itself and test
# directories -- exactly the set CE-20 requires to have zero import of
# conversation_engine.
_CHECKED_PACKAGES = [
    "application", "bot", "trust_manager", "penalty_engine", "recovery_plan",
    "goal_management", "task_catalog", "advanced_mode", "infrastructure", "ai",
]


def _imports_conversation_engine(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name == "conversation_engine" or alias.name.startswith("conversation_engine.") for alias in node.names):
                return True
        if isinstance(node, ast.ImportFrom):
            if node.module and (node.module == "conversation_engine" or node.module.startswith("conversation_engine.")):
                return True
    return False


class TestNoExistingPackageImportsConversationEngine:
    def test_static_ast_scan(self) -> None:
        offending: list[str] = []
        for package in _CHECKED_PACKAGES:
            package_dir = PROJECT_ROOT / package
            if not package_dir.is_dir():
                continue
            for py_file in package_dir.rglob("*.py"):
                if _imports_conversation_engine(py_file):
                    offending.append(str(py_file.relative_to(PROJECT_ROOT)))
        assert offending == [], f"Found conversation_engine import(s) in files that must not have one: {offending}"


class TestExistingCommandBehaviorUnaffected:
    """Constructs a real ApplicationService (which now also constructs
    conversation_engine-adjacent... no, actually nothing --
    ApplicationService itself has no conversation_engine dependency at
    all, confirmed by the AST scan above) and exercises a few command
    paths directly."""

    @pytest.fixture
    def service(self, tmp_path: Path) -> ApplicationService:
        core = CoreDatabase(tmp_path / "test.db")
        migrations_dir = PROJECT_ROOT / "database" / "migrations"
        with core.raw_connection() as conn:
            for path in sorted(migrations_dir.glob("*.sql")):
                conn.executescript(path.read_text(encoding="utf-8"))
        return ApplicationService(core.db_path, core=core)

    def _onboard(self, service: ApplicationService, external_user_id: str = "1") -> None:
        for text in ("anything", "english", "neutral", "alex"):
            service.handle_message(
                IncomingMessage(channel="discord", external_user_id=external_user_id, text=text, received_at=FIXED_TIME),
            )

    def test_help_command_unchanged(self, service: ApplicationService) -> None:
        self._onboard(service)
        result = service.handle_message(
            IncomingMessage(channel="discord", external_user_id="1", text="help", received_at=FIXED_TIME),
        )
        assert "Available commands:" in result.text

    def test_status_command_unchanged(self, service: ApplicationService) -> None:
        self._onboard(service)
        result = service.handle_message(
            IncomingMessage(channel="discord", external_user_id="1", text="status", received_at=FIXED_TIME),
        )
        assert isinstance(result.text, str) and result.text

    def test_mode_status_command_unchanged(self, service: ApplicationService) -> None:
        self._onboard(service)
        result = service.handle_message(
            IncomingMessage(channel="discord", external_user_id="1", text="mode status", received_at=FIXED_TIME),
        )
        assert "current mode: standard" in result.text.lower()

    def test_unrecognized_text_unchanged(self, service: ApplicationService) -> None:
        self._onboard(service)
        result = service.handle_message(
            IncomingMessage(channel="discord", external_user_id="1", text="asdkfjhasdkjfh", received_at=FIXED_TIME),
        )
        assert "don't recognize" in result.text.lower()

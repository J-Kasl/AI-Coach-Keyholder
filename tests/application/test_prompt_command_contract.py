"""
tests/application/test_prompt_command_contract.py

Prompt Hygiene & Command Contract Guard (post-Slice-D follow-up).

Verifies that every command Conversation Behavior Policy tells the
model it may recommend (`conversation_engine.prompt_builder.KEYHOLDER_COMMAND_GUIDANCE`)
is actually registered in the real, production `ApplicationService`
router -- via `CommandRouter.help_text()`, its own existing public
surface (Variant B per explicit review instruction -- no new
CommandRouter read API was needed).

Deliberately does NOT check the reverse direction: `status`,
`preferences`, and the `mode ...` family are real, registered
commands that are intentionally NOT part of this curated keyholder
subset -- this guard only detects DRIFT of the eight commands the
prompt actually recommends, it is not a completeness check of the
whole application command surface.

This test file itself is the one place allowed to import BOTH
`conversation_engine` and `application` -- it exists specifically to
verify the boundary between them, not to create a new production
dependency in either direction (verified structurally by
`tests/conversation_engine/test_system_independence.py`'s own guard,
unaffected by this file).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from application.service import ApplicationService
from conversation_engine.prompt_builder import KEYHOLDER_COMMAND_GUIDANCE
from infrastructure.database import Database as CoreDatabase


def _apply_migrations(core: CoreDatabase) -> None:
    migrations_dir = Path(__file__).parent.parent.parent / "database" / "migrations"
    with core.raw_connection() as conn:
        for path in sorted(migrations_dir.glob("*.sql")):
            conn.executescript(path.read_text(encoding="utf-8"))


@pytest.fixture
def service(tmp_path: Path) -> ApplicationService:
    core = CoreDatabase(tmp_path / "test.db")
    _apply_migrations(core)
    return ApplicationService(core.db_path, core=core)


class TestEveryPromptRecommendedCommandIsActuallyRegistered:
    def test_all_eight_keyholder_commands_exist_in_the_real_router(self, service: ApplicationService) -> None:
        help_text = service.router.help_text()
        missing = [
            command for command in KEYHOLDER_COMMAND_GUIDANCE
            if f"  {command} \u2014 " not in help_text
        ]
        assert missing == [], f"Prompt recommends commands that are not actually registered: {missing}"

    def test_guidance_tuple_has_exactly_eight_commands(self) -> None:
        """A sanity bound -- if this list quietly grows/shrinks, that's
        worth a deliberate review, not a silent drift."""
        assert len(KEYHOLDER_COMMAND_GUIDANCE) == 8


class TestExtraRouterCommandsAreIntentionallyNotRequired:
    """The guard only checks prompt-guidance -> router direction. These
    commands existing in the router but NOT in KEYHOLDER_COMMAND_GUIDANCE
    is expected and correct, not a violation -- proven directly, not
    just asserted in a comment."""

    def test_status_preferences_and_mode_are_registered_but_not_in_the_keyholder_subset(
        self, service: ApplicationService,
    ) -> None:
        help_text = service.router.help_text()
        for extra_command in ("status", "preferences", "mode status"):
            assert f"  {extra_command} \u2014 " in help_text  # really registered
            assert extra_command not in KEYHOLDER_COMMAND_GUIDANCE  # deliberately excluded from prompt guidance

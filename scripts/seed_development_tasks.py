"""
scripts/seed_development_tasks.py

A standalone, explicitly-invoked maintenance script -- run manually:

    python3 -m scripts.seed_development_tasks [--db-path PATH]

Deliberately NOT wired into bot/discord_bot.py's own startup
(main() never creates domain data today; adding "and also seed some
tasks" there would be a silent side effect outside that function's own
responsibility) and NOT a migration (migrations are schema, not
content -- seeding two development fixture templates has no place in
database/migrations/, which this project's own convention reserves for
schema changes recorded in schema_version).

Deliberately does NOT use core.config.Config.load() -- that requires
DISCORD_TOKEN (raises ConfigError otherwise), an unrelated dependency
for a database-only tool. Reads DB_PATH directly (or accepts
--db-path), falling back to the same DEFAULT_DB_PATH the rest of the
project uses.

Idempotent: checks TaskCatalog.get_current_version() before each
create_template() call -- never relies on a caught exception as
control flow, and running this twice against the same database creates
nothing the second time.
"""

from __future__ import annotations

import argparse
import os
from datetime import datetime
from pathlib import Path

from core.config import DEFAULT_DB_PATH
from infrastructure.clock import SystemClock
from infrastructure.database import Database as CoreDatabase
from task_catalog.models import LockRequirement, TaskInstanceRole
from task_catalog.repository import TaskCatalog, TaskCatalogAdministration

__all__ = ["DEV_SEED_CONSENT_ID", "seed_development_templates"]

# NOT a real user consent -- task_catalog's own consent field is an
# unconditionally-required, unvalidated audit-trail string (the same
# as everywhere else in this project, e.g. lock_state/task_runtime's
# own `_require_consent_id()`, which only checks "non-empty"). This is
# a clearly-labeled provenance marker for development fixture data,
# not a claim that any user consented to anything.
DEV_SEED_CONSENT_ID = "system:dev_seed"

# title/instructions (Candidate B): plain, self-contained descriptions,
# deliberately never mentioning deterministic command vocabulary (e.g.
# "task complete") -- that belongs solely to
# conversation_engine.prompt_builder.KEYHOLDER_COMMAND_GUIDANCE and
# application/router.py's own registered commands; duplicating it
# here would create a second, driftable source of command truth.
# The locked variant's wording preserves the exact epistemic
# distinction lock_state/models.py itself insists on: eligibility is
# based on the user's own report, never framed as independent
# physical verification.
_SEED_DEFINITIONS: dict[str, dict] = {
    "dev-seed-basic-chore": dict(
        title="Tidy one surface",
        instructions=(
            "Pick one flat surface in your home, such as a desk, counter, or table. "
            "Put loose items away and wipe the surface down if needed. Spend about "
            "ten minutes on it."
        ),
        category="chore", difficulty="easy", effort="low", duration_minutes=10,
        required_equipment=(), required_privacy="none", required_context="home",
        safety_classification="safe", eligible_instance_roles=(TaskInstanceRole.PRIMARY,),
        eligible_operating_modes=("standard", "advanced"), completion_requirements={},
        verification_requirements={}, reflection_requirements=None,
        lock_requirement=LockRequirement.NONE,
    ),
    "dev-seed-locked-chore": dict(
        title="Tidy one surface (locked-only)",
        instructions=(
            "Pick one flat surface in your home, such as a desk, counter, or table. "
            "Put loose items away and wipe the surface down if needed. Spend about "
            "ten minutes on it. This task is only offered while you have reported "
            "yourself as locked; that eligibility is based on your user-reported "
            "lock state, not on independent physical verification."
        ),
        category="chore", difficulty="easy", effort="low", duration_minutes=10,
        required_equipment=(), required_privacy="none", required_context="home",
        safety_classification="safe", eligible_instance_roles=(TaskInstanceRole.PRIMARY,),
        eligible_operating_modes=("standard", "advanced"), completion_requirements={},
        verification_requirements={}, reflection_requirements=None,
        lock_requirement=LockRequirement.REQUIRES_LOCKED,
    ),
    # First real development catalog content (Task Catalog Content
    # Proposal, Option A -- approved 2026-09 including the single
    # physical-activity item, "short-walk"). Ten new templates,
    # additive alongside the two original dev-seed-* fixtures above,
    # which remain untouched. Every one of these uses the same
    # unused-today descriptive fields (required_equipment/privacy/
    # context, safety_classification, effort) at the same values as
    # the original two, since nothing in task_runtime's own eligibility
    # logic reads them yet (see task_catalog/README.md). Only
    # "locked-reflection-entry" carries LockRequirement.REQUIRES_LOCKED
    # -- every other new template is LockRequirement.NONE.
    "tidy-one-surface": dict(
        title="Tidy one surface",
        instructions=(
            "Pick one flat surface in your home, such as a desk, counter, or table. "
            "Put loose items away and wipe the surface down if needed. Spend about "
            "ten minutes on it."
        ),
        category="chore", difficulty="easy", effort="low", duration_minutes=10,
        required_equipment=(), required_privacy="none", required_context="home",
        safety_classification="safe", eligible_instance_roles=(TaskInstanceRole.PRIMARY,),
        eligible_operating_modes=("standard", "advanced"),
        completion_requirements={"description": "The chosen surface is visibly clear and wiped down."},
        verification_requirements={}, reflection_requirements=None,
        lock_requirement=LockRequirement.NONE,
    ),
    "clear-one-inbox": dict(
        title="Clear one inbox to zero",
        instructions=(
            "Pick one inbox -- email, a messaging app, or physical mail -- and "
            "process it down to zero: reply, file, delete, or archive each item. "
            "Spend up to twenty minutes on it."
        ),
        category="organization", difficulty="medium", effort="low", duration_minutes=20,
        required_equipment=(), required_privacy="none", required_context="home",
        safety_classification="safe", eligible_instance_roles=(TaskInstanceRole.PRIMARY,),
        eligible_operating_modes=("standard", "advanced"),
        completion_requirements={"description": "The chosen inbox has no unprocessed items left."},
        verification_requirements={}, reflection_requirements=None,
        lock_requirement=LockRequirement.NONE,
    ),
    "five-minute-declutter": dict(
        title="Five-minute declutter sprint",
        instructions=(
            "Set a timer for five minutes and put away as many out-of-place items "
            "as you can find in one room. When the timer ends, stop."
        ),
        category="chore", difficulty="easy", effort="low", duration_minutes=5,
        required_equipment=(), required_privacy="none", required_context="home",
        safety_classification="safe", eligible_instance_roles=(TaskInstanceRole.PRIMARY,),
        eligible_operating_modes=("standard", "advanced"),
        completion_requirements={"description": "A five-minute decluttering sprint was completed in one room."},
        verification_requirements={}, reflection_requirements=None,
        lock_requirement=LockRequirement.NONE,
    ),
    "plan-tomorrow": dict(
        title="Plan tomorrow in three lines",
        instructions=(
            "Write down the three most important things you want to get done "
            "tomorrow. Keep it short -- one line each. Put the list somewhere you "
            "will actually see it in the morning."
        ),
        category="planning", difficulty="easy", effort="low", duration_minutes=10,
        required_equipment=(), required_privacy="none", required_context="home",
        safety_classification="safe", eligible_instance_roles=(TaskInstanceRole.PRIMARY,),
        eligible_operating_modes=("standard", "advanced"),
        completion_requirements={"description": "A three-item plan for tomorrow has been written down."},
        verification_requirements={}, reflection_requirements=None,
        lock_requirement=LockRequirement.NONE,
    ),
    "evening-reset": dict(
        title="Evening reset routine",
        instructions=(
            "Spend ten minutes preparing for tomorrow: lay out what you need, tidy "
            "your immediate space, and set out anything you tend to forget. The "
            "goal is a calmer start tomorrow, not a perfect one."
        ),
        category="routine", difficulty="easy", effort="low", duration_minutes=10,
        required_equipment=(), required_privacy="none", required_context="home",
        safety_classification="safe", eligible_instance_roles=(TaskInstanceRole.PRIMARY,),
        eligible_operating_modes=("standard", "advanced"),
        completion_requirements={"description": "An evening reset routine was completed."},
        verification_requirements={}, reflection_requirements=None,
        lock_requirement=LockRequirement.NONE,
    ),
    "focused-work-block": dict(
        title="One focused block, no phone",
        instructions=(
            "Pick one task you have been putting off. Put your phone out of reach, "
            "set a timer for twenty-five minutes, and work on only that task until "
            "the timer ends."
        ),
        category="focus", difficulty="medium", effort="low", duration_minutes=25,
        required_equipment=(), required_privacy="none", required_context="home",
        safety_classification="safe", eligible_instance_roles=(TaskInstanceRole.PRIMARY,),
        eligible_operating_modes=("standard", "advanced"),
        completion_requirements={"description": "A single twenty-five-minute focused work block was completed on one task."},
        verification_requirements={}, reflection_requirements=None,
        lock_requirement=LockRequirement.NONE,
    ),
    "finish-one-postponed-task": dict(
        title="Finish one small postponed task",
        instructions=(
            "Think of one small task you have been putting off for at least a "
            "week -- something that takes under thirty minutes. Do it now, start "
            "to finish."
        ),
        category="organization", difficulty="medium", effort="low", duration_minutes=30,
        required_equipment=(), required_privacy="none", required_context="home",
        safety_classification="safe", eligible_instance_roles=(TaskInstanceRole.PRIMARY,),
        eligible_operating_modes=("standard", "advanced"),
        completion_requirements={"description": "One previously postponed small task was completed start to finish."},
        verification_requirements={}, reflection_requirements=None,
        lock_requirement=LockRequirement.NONE,
    ),
    "short-walk": dict(
        title="Take a short walk",
        instructions=(
            "Go for an ordinary walk outside or, if that is not possible, walk "
            "indoors for the same amount of time. Ten minutes is enough -- this "
            "is about movement, not exercise performance."
        ),
        category="movement", difficulty="easy", effort="low", duration_minutes=10,
        required_equipment=(), required_privacy="none", required_context="home",
        safety_classification="safe", eligible_instance_roles=(TaskInstanceRole.PRIMARY,),
        eligible_operating_modes=("standard", "advanced"),
        completion_requirements={"description": "A short, ordinary walk was completed."},
        verification_requirements={}, reflection_requirements=None,
        lock_requirement=LockRequirement.NONE,
    ),
    "evening-journal-entry": dict(
        title="Write one journal entry",
        instructions=(
            "Spend ten minutes writing about how today actually went -- what "
            "worked, what didn't, and one thing you'd do differently tomorrow. No "
            "particular format is required."
        ),
        category="reflection", difficulty="easy", effort="low", duration_minutes=10,
        required_equipment=(), required_privacy="none", required_context="home",
        safety_classification="safe", eligible_instance_roles=(TaskInstanceRole.PRIMARY,),
        eligible_operating_modes=("standard", "advanced"),
        completion_requirements={"description": "One journal entry reflecting on the day was written."},
        verification_requirements={}, reflection_requirements=None,
        lock_requirement=LockRequirement.NONE,
    ),
    "locked-reflection-entry": dict(
        title="Reflection journal entry (locked-only)",
        instructions=(
            "Spend ten minutes writing about how you're feeling about your "
            "current commitment today -- what's easy, what's hard, and one thing "
            "you're proud of. This task is only offered while you have reported "
            "yourself as locked; that eligibility is based on your user-reported "
            "lock state, not on independent physical verification."
        ),
        category="reflection", difficulty="easy", effort="low", duration_minutes=10,
        required_equipment=(), required_privacy="none", required_context="home",
        safety_classification="safe", eligible_instance_roles=(TaskInstanceRole.PRIMARY,),
        eligible_operating_modes=("standard", "advanced"),
        completion_requirements={"description": "One reflection journal entry was written."},
        verification_requirements={}, reflection_requirements=None,
        lock_requirement=LockRequirement.REQUIRES_LOCKED,
    ),
}


def seed_development_templates(
    admin: TaskCatalogAdministration, catalog: TaskCatalog, *, via_consent_id: str, now: datetime,
) -> list[str]:
    """Returns the template_ids actually created this call -- an empty
    list if everything already existed. Idempotent via an explicit
    existence check (catalog.get_current_version()) before each
    create_template() call, never a caught "already exists" exception
    used as control flow."""
    created: list[str] = []
    for template_id, kwargs in _SEED_DEFINITIONS.items():
        if catalog.get_current_version(template_id) is not None:
            continue
        admin.create_template(
            template_id=template_id, **kwargs, created_via_consent_id=via_consent_id, now=now,
        )
        created.append(template_id)
    return created


def _resolve_db_path(cli_db_path: str | None) -> Path:
    if cli_db_path:
        return Path(cli_db_path)
    env_value = os.environ.get("DB_PATH")
    return Path(env_value) if env_value else DEFAULT_DB_PATH


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed two neutral development/test task templates.")
    parser.add_argument("--db-path", default=None, help="Override the database path (defaults to DB_PATH env var, then the project default).")
    args = parser.parse_args()

    db_path = _resolve_db_path(args.db_path)
    core = CoreDatabase(db_path)
    admin = TaskCatalogAdministration(db_path, core=core)
    catalog = TaskCatalog(db_path, core=core)

    created = seed_development_templates(
        admin, catalog, via_consent_id=DEV_SEED_CONSENT_ID, now=SystemClock().now(),
    )
    if created:
        print(f"Created {len(created)} development template(s): {', '.join(created)}")
    else:
        print("All development templates already exist -- nothing created.")


if __name__ == "__main__":
    main()

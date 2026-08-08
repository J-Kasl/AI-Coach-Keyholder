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

_SEED_DEFINITIONS: dict[str, dict] = {
    "dev-seed-basic-chore": dict(
        category="chore", difficulty="easy", effort="low", duration_minutes=10,
        required_equipment=(), required_privacy="none", required_context="home",
        safety_classification="safe", eligible_instance_roles=(TaskInstanceRole.PRIMARY,),
        eligible_operating_modes=("standard", "advanced"), completion_requirements={},
        verification_requirements={}, reflection_requirements=None,
        lock_requirement=LockRequirement.NONE,
    ),
    "dev-seed-locked-chore": dict(
        category="chore", difficulty="easy", effort="low", duration_minutes=10,
        required_equipment=(), required_privacy="none", required_context="home",
        safety_classification="safe", eligible_instance_roles=(TaskInstanceRole.PRIMARY,),
        eligible_operating_modes=("standard", "advanced"), completion_requirements={},
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

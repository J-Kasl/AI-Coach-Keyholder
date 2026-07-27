"""
infrastructure/time_format.py

The single canonical implementation of the datetime <-> ISO-8601-TEXT
conversion every repository in this system needs for storing
timestamps in SQLite TEXT columns. Extracted during the final
architecture review pass (Phase 2.7) -- `trust_manager/repository.py`,
`penalty_engine/repository.py`, `recovery_plan/repository.py`,
`infrastructure/outbox.py`, and `infrastructure/startup_lease.py` had
each independently defined an identical private `_iso()`/`_parse_iso()`
pair. Consolidating them here removes that duplication without
changing behavior anywhere -- every call site's contract (a
timezone-aware UTC `datetime` in, a `...Z`-suffixed ISO 8601 string
out, and the exact reverse) is unchanged.
"""

from __future__ import annotations

from datetime import datetime, timezone

__all__ = ["iso", "parse_iso"]


def iso(dt: datetime) -> str:
    """Formats a timezone-aware datetime as the ISO 8601 string this
    system stores in SQLite TEXT columns (UTC, `Z` suffix instead of
    `+00:00`)."""
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_iso(value: str) -> datetime:
    """Parses that same ISO 8601 string back into a timezone-aware
    (UTC) datetime."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))

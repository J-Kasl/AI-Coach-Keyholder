"""
infrastructure

Shared, cross-cutting components used by every domain module (Trust
Manager, Penalty Engine, Activity Authorization, Hygiene Privilege,
Goal Management, Recovery Plan, Extension) — never a domain module
itself, and never owning any domain state or business decision.

This is the code-level home for what `implementation_conventions.md`
Part I calls "shared infrastructure, not any one domain": the `Clock`,
and — in later phases — the database wrapper, the transaction helper,
the transactional outbox, the consumer framework, and the startup
orchestrator.

See `infrastructure/README.md` for the current status of this package.
"""

from __future__ import annotations

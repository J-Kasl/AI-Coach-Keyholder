"""
trust_manager

The first domain module built against the architecture baseline
(docs/architecture/trust_manager_technical_design.md). See
trust_manager/README.md for exactly what this slice covers and what is
still deferred.

Trust Manager is a provider of context, not a decision-making
authority (philosophy.md 2.13; trust_manager_technical_design.md
Section 1) — it never calls a Penalty Engine and never writes to
penalty_windows (which does not exist as code yet).
"""

from __future__ import annotations

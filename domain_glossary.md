# Glossary

Version: 1.0

---

# Purpose

This glossary defines the official terminology used throughout the project.

Every domain concept has exactly one official English name.
These names are used consistently in:

- documentation
- source code
- database schemas
- events
- APIs
- user interface (where applicable)

Localized user interfaces may translate surrounding text, but official domain terms remain unchanged.

---

# Terminology Rules

## Official Domain Terms

Official domain terms are considered proper names of system concepts.

They are never translated.

Example:

✔ Penalty Window is currently active.

✔ Complete your Recovery Task to reduce the remaining restriction period.

---

## Localized Text

Only explanatory text is localized.

Example (Czech):

Penalty Window je momentálně aktivní.

Pokud dokončíš Recovery Task, může dojít ke zkrácení období omezení.

---

## Naming Principles

Every new domain term should:

- be concise
- describe a single concept
- remain stable over time
- be understandable without additional explanation
- work naturally in documentation and code

---

# Domain Terms

## Activity Authorization

Module responsible for determining whether a regulated activity may occur.

Status:
Official domain term.

Localized:
No.

---

## Extension

An increase in the duration of an active Penalty Window.

Status:
Official domain term.

Localized:
No.

---

## Freeze

A temporary pause of the Penalty Window countdown.

While frozen, remaining duration does not decrease.

Status:
Official domain term.

Localized:
No.

---

## Incident

A confirmed user behavior that violates a system rule.

An Incident may trigger actions in one or more modules.

Status:
Official domain term.

Localized:
No.

---

## Penalty Engine

Module responsible for managing Penalty Windows.

Responsibilities include:

- lifecycle
- countdown
- freezes
- extensions
- recovery
- state transitions

Status:
Official domain term.

Localized:
No.

---

## Penalty Window

A temporary restriction period enforced by the Penalty Engine.

Status:
Official domain term.

Localized:
No.

---

## Recovery Task

A task assigned to support behavioral recovery.

Completion may influence Penalty Window duration or other system behavior.

Status:
Official domain term.

Localized:
No.

---

## Trust Evidence

Immutable evidence affecting trust evaluation.

Status:
Official domain term.

Localized:
No.

---

## Trust Manager

Module responsible for trust evaluation.

Status:
Official domain term.

Localized:
No.

---

# Technical Terms

The following software engineering terms follow their standard English meaning.

They may appear in documentation without translation.

Examples include:

- Event
- Event Handler
- Outbox
- Scheduler
- Snapshot
- Replay
- Commit
- Rollback
- Transaction
- Idempotency
- Startup Recovery
- Checkpoint
- Session
- Lease
- Clock

---

# Reserved Terms

The following names are reserved.

Alternative names should not be introduced.

| Official | Do Not Use |
|-----------|------------|
| Penalty Window | Restriction Window, Penalty Period |
| Trust Evidence | Trust Record, Trust Log |
| Recovery Task | Recovery Mission, Recovery Activity |
| Freeze | Pause Period, Suspension |
| Extension | Prolongation, Delay |
| Incident | Offense, Violation Record |

---

# Future Terms

This section contains placeholders for concepts that may be introduced later.

Examples:

- Decision Orchestrator
- Extension Engine
- User Profile
- Notification Service
- Localization Service

These terms are not yet part of the official domain model.
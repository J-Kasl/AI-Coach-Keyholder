# Glossary

Version: 1.4

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

## Discretionary Hygiene Break

A planned break intended for more thorough care and inspection of the
body than is possible with a device attached. Subject to the privilege
system, unlike Mandatory Hygiene/Health Access.

Status:
Official domain term.

Localized:
No.

---

## Effective Hygiene Policy

The Discretionary Hygiene Break policy actually applied at a given
moment, derived from Hygiene Trust Level and any active Penalty Window
Override.

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

## Goal

An agreed direction of development whose purpose is to improve the
user's long-term trajectory. A Goal does not define a binding
behavioral boundary — see Rule.

Status:
Official domain term.

Localized:
No.

---

## Goal Failure

The Goal Outcome value indicating that a Goal's target was not met for
an evaluation period.

A Goal Failure is never itself an Incident and never itself creates a
Penalty Window.

Status:
Official domain term.

Localized:
No.

---

## Goal Outcome

The result of evaluating a Goal's target against a specific period:
Goal Success, Goal Failure, or a partial result that is neither.

Status:
Official domain term.

Localized:
No.

---

## Goal Success

The Goal Outcome value indicating that a Goal's target was met for an
evaluation period.

Status:
Official domain term.

Localized:
No.

---

## Hygiene Trust Level

A level derived from Hygiene Trust that determines the default
Discretionary Hygiene Break policy in the absence of an active Penalty
Window Override.

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

## Mandatory Hygiene/Health Access

Access to address a health or safety need — including using the
toilet, or addressing pain, swelling, numbness, or injury. Never
subject to the privilege system and always immediately available.

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

## Penalty Window Override

A temporary Discretionary Hygiene Break policy that applies while a
relevant Penalty Window is active, in place of the Hygiene Trust Level.
Does not modify the Hygiene Trust Level itself.

Status:
Official domain term.

Localized:
No.

---

## Recovery Plan

The set of Recovery Tasks and their progress for a specific Penalty
Window, designed by the Coach. Exists only for the lifetime of that
Penalty Window.

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

## Rule

A binding behavioral boundary the user has explicitly agreed to. A
confirmed Rule violation is an Incident. A Rule does not define a
desired direction of development — see Goal.

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
| Recovery Plan | Recovery Roadmap, Rehabilitation Plan |
| Freeze | Pause Period, Suspension |
| Extension | Prolongation, Delay |
| Incident | Offense, Violation Record |
| Goal | Objective, Target, Habit |
| Rule | Requirement, Constraint, Commitment |
| Goal Failure | Missed Goal, Goal Violation |

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
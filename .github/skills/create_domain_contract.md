---
name: create_domain_contract
description: Use when defining a typed domain contract — an application use case (business flow) or a repository interface (persistence boundary) — with clean architecture boundaries.
---

# Skill: create_domain_contract

## Purpose

Create a typed domain contract aligned with clean architecture boundaries. This skill
covers two contract shapes selected via `contract_type`:

- `use_case` — an application use case that expresses an explicit business flow.
- `repository` — a repository/persistence interface that isolates infrastructure
  details from domain and application logic.

## Required Input

- `contract_type`: `use_case` | `repository`.
- Common: entity/aggregate or business goal, and the typed input/output models.
- For `use_case`: actor(s) involved and the expected business outcome.
- For `repository`: required operations (CRUD/query) and consistency/transactional
  constraints.

## Output Format

- Contract name (use case name, or interface/protocol name).
- Python module/class/function (or protocol) proposal.
- Typed input/output models involved in each operation.
- For `use_case`: ordered execution flow and error-handling strategy.
- For `repository`: method signatures, adapter implementation guidance (infra side),
  and a contract-behavior test strategy.

## Mode

- **use_case** — model the business flow as an application-layer use case. Keep the
  flow ordered and explicit; define error handling.
- **repository** — model the persistence boundary as an interface/protocol. Keep the
  domain independent from infrastructure implementations; prefer minimal, explicit
  method contracts.

## Execution Rules

1. Follow `.github/architecture.md`.
2. Apply `.github/standards.md`.
3. Respect `.github/domain-boundaries.md` (domain must not depend on infrastructure).
4. Use absolute imports and type hints.
5. Keep naming/docstrings in English.

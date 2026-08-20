# Architecture Governance

This document defines the architectural frame for AI-assisted code generation.

## Core Principles

- Follow SRP (Single Responsibility Principle) for modules and classes.
- Keep business logic separated from infrastructure and configuration.
- Prefer explicit contracts (interfaces/protocols, typed boundaries).
- Avoid over-engineering; split modules only when lifecycle/responsibility differs.

## Preferred Layering

Use a simple, practical clean architecture style:

1. Domain layer: entities, value objects, core rules.
2. Application layer: use cases and orchestration.
3. Infrastructure layer: framework, I/O, DB, APIs, providers.

Rules:

- Domain must not depend on infrastructure.
- Application can depend on domain abstractions.
- Infrastructure depends on domain/application contracts.

## Python Structure Guidance

- Production code in `src/`.
- Tests in `tests/`.
- Exploration only in `notebooks/`.
- Prompts managed under `src/agent_rag/prompts/` when present.

## Decision Policy

When a generation request is ambiguous:

- Choose the simplest architecture-compatible solution.
- Preserve existing project style and naming.
- Minimize file churn and unrelated refactors.

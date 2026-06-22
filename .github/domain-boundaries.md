# Domain Boundaries

This document defines module and data boundaries for consistent generation.

## Repository Zones

- `src/`: production code only.
- `tests/`: automated tests.
- `notebooks/`: exploratory work and prototyping.

## Data Zones

- `data/raw/`: immutable source data (read-only for pipelines).
- `data/processed/`: transformed/feature-ready data.

Rules:

- Never overwrite raw data in-place.
- Write transformations to processed artifacts.

## Prompt and Agent Zones

- Prompt logic should live under `src/agent_rag/prompts/` when applicable.
- Keep prompt templates separate from execution/runtime code.

## Dependency Boundaries

- Domain/application modules should not depend directly on notebook code.
- Test helpers should not leak into production modules.
- Infrastructure concerns should stay out of domain core.

## Change Boundaries

When implementing tasks:

- Change only files related to the requested scope.
- Avoid cross-cutting refactors unless explicitly requested.

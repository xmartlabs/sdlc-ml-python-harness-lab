# Engineering Standards

This document defines coding and operational standards across tools.

## Language Policy

- User interaction language follows user language.
- Code artifacts are always in English:
  - identifiers
  - docstrings
  - comments
  - generated technical docs

## Python and Environment

- Use Python for generated implementation by default.
- Use `uv` workflows via Makefile commands.
- Do not recommend direct `pip` usage for project dependencies.

## Command Policy

Before suggesting project commands, inspect and prioritize `Makefile` targets.

Primary commands:

- `make install`
- `make add PKG=<package>`
- `make format` / `make fix` — local-only (mutate the tree).
- `make check` — read-only quality gate (CI-safe).
- `make ci` — full read-only pipeline (`check` + `check-sync`).

## Code Quality

- Enforce type hints whenever practical; `make typecheck` runs `mypy`.
- Prefer `Pydantic` for structured validation/configuration.
- Use Ruff for linting/formatting workflows.
- Run security checks (`bandit`) through `make check`.
- CI is read-only: never rely on CI to format or fix code — do it locally
  before pushing (see `.github/automation.md`).

## Imports

- Use absolute imports only.
- Avoid relative imports (`from .x import y`, `from ..x import y`).

## Prompt Design

For complex LLM prompts, prefer structured XML-like sections:

- `<thinking>`
- `<context>`
- `<instructions>`

## Validation Checklist

Before finalizing generated work:

1. Code artifacts are in English.
2. Relevant quality checks ran (`make check` and/or targeted tests).
3. Data flow respects raw vs processed boundaries.
4. Implementation changes include/update documentation under `docs/`.

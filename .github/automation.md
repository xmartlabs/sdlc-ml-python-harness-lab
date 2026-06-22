# Automation Policy

This file defines Level 3 enforcement so quality does not depend on model behavior.

## Required Enforcement

- Strict lint and formatting checks.
- CI pipelines for lint/test/security/type-checking.
- Structure validation checks.
- Skill-sync drift gate: generated adapter/skill artifacts must be current.
- PR automation (status checks, optional bots).
- Documentation updates in `docs/` when `src/` or `tests/` change.

## CI Is Read-Only

- CI **verifies**, it never mutates the working tree.
- `make check` is the read-only gate: `ruff format --check`, `ruff check`,
  `bandit`, `mypy`, and `pytest` (no auto-formatting, no auto-fixing).
- `make check-sync` fails if any generated skill artifact or adapter skill
  region is stale relative to the governed sources in `.github/`.
- `make format` and `make fix` are **local-only** — run them before pushing.

## Baseline Commands

- `make check` — read-only quality gate (use in CI).
- `make check-sync` — skill-sync drift gate (use in CI).
- `make ci` — full read-only pipeline (`check` + `check-sync`).
- `make format` / `make fix` — local-only, mutate the tree.
- `make lint` / `make test` — local helpers (mutating variants).

## Implementation Notes

- Keep enforcement deterministic and reproducible.
- Prefer failing fast in CI for boundary or quality violations.
- Keep local workflow aligned with CI by running `make ci` before pushing.

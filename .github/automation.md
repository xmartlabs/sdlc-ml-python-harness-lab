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
- `make ci` — full read-only pipeline (`check` + `check-sync` + `check-frontend`).
- `make format` / `make fix` — local-only, mutate the tree.
- `make test` — all automated tests with coverage; propagates pytest failures and
  does not format or fix source files.
- `make lint` — local helper (reports Bandit findings without failing the target).

## Local Git Gates

`make setup-hooks` installs `pre-commit`, `commit-msg`, and `pre-push` hooks.
Commits run only the file hooks on staged files (seconds). The local quality
hook runs `make ci` before pushes, including the complete Python test suite (in
parallel with `pytest-xdist`), skill-sync checks, and frontend type/build checks,
and propagates a failed gate. A push changing only `docs/` or `memory/` Markdown
runs `make check-sync` instead.
Formatting and autofix hooks run only at commit time; push checks do not edit
source files. This prevents a first branch push from rewriting unrelated
benchmarks, fixtures, or vendored skills while checking the branch's history.

Local Git hooks can be bypassed or removed; they supplement required CI checks,
not replace them. Docker/model smoke tests are separate operational checks and
are not implied by a passing unit/service-integration suite.

## Implementation Notes

- Keep enforcement deterministic and reproducible.
- Prefer failing fast in CI for boundary or quality violations.
- Keep local workflow aligned with CI by running `make ci` before pushing.

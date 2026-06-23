# Patterns

> Recurring conventions and idioms specific to this codebase. New code should match
> what is documented here. One pattern per entry.

## Code & commands

- **CLI-first workflow.** Prefer `make` targets and `uv` over ad-hoc commands; check
  the `Makefile` before suggesting a command. See `.github/standards.md`.
- **Absolute imports only.** No relative imports inside `src/`.
- **English-only code artifacts.** Identifiers, docstrings, comments, and docs are in
  English regardless of conversation language.
- **src-layout + clean architecture.** Code lives under `src/`; respect the
  domain / application / infrastructure boundaries in `.github/architecture.md` and
  `.github/domain-boundaries.md`.

## Quality

- **Read-only CI.** CI never mutates the tree. Run `make fix` and `make format`
  locally before pushing; CI runs `make check` + `make check-sync`.
- **Docs follow code.** Changes under `src/` or `tests/` must come with a `docs/`
  update (enforced by `.github/workflows/docs-quality-guardrails.yml`).

<!-- Add new patterns above this line -->

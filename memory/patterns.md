# Patterns

> Recurring conventions and idioms specific to this codebase. New code should match
> what is documented here. One pattern per entry.

## Code & commands

- **CLI-first workflow.** Prefer `make` targets and `uv` over ad-hoc commands; check
  the `Makefile` before suggesting a command. See `.github/standards.md`.
- **Absolute imports only.** No relative imports inside `src/`.
- **English-only code artifacts.** Identifiers, docstrings, comments, docs, AND all
  user-facing UI/UX copy (labels, tooltips, empty states, aria-labels) are in
  English regardless of conversation language. Note: some older frontend screens
  still carry Spanish strings — new/edited copy must be English; sweep legacy
  strings opportunistically when touching a screen.
- **src-layout + clean architecture.** Code lives under `src/`; respect the
  domain / application / infrastructure boundaries in `.github/architecture.md` and
  `.github/domain-boundaries.md`.

## Quality

- **Read-only CI.** CI never mutates the tree. Run `make fix` and `make format`
  locally before pushing; CI runs `make check` + `make check-sync`.
- **Docs follow code.** Changes under `src/` or `tests/` must come with a `docs/`
  update (enforced by `.github/workflows/docs-quality-guardrails.yml`).
- **Onboarding docs follow integrations.** The lab is meant to be shared with other
  developers, so whenever a new tool/harness/backend is integrated or its setup
  changes (e.g. wiring codex into Docker, a new gateway path, a new credential),
  the deliverable INCLUDES updating the setup guides in the same change:
  `docs/onboarding.md` (credential path + the "what each path enables" feature
  matrix + troubleshooting table), the README docs map / Quick Start if a new path
  appears, and `docs/harness-adapters.md` for harness wiring. A working integration
  that only the author can configure is not done.

<!-- Add new patterns above this line -->

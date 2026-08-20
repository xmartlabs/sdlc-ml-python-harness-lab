# Contributing

Thanks for considering a contribution. This repository is a lab: it measures
agentic coding harnesses, so its own quality gates are part of the experiment.
A change that cannot be reproduced or verified is not useful here, however good
the idea behind it.

## Setup

```bash
make install        # uv-managed venv + dev dependencies
cp .env.example .env
make app-up         # API + worker + frontend → http://localhost:5173
```

[docs/onboarding.md](docs/onboarding.md) covers credentials, local model
runtimes and troubleshooting. You do not need a gateway or a GPU to work on the
app itself — runs can be created with `dry_run`, and the test suite makes no
network calls and reads no credentials: it stubs the repository `.env` with a
fixture (`tests/harness_lab_app/conftest.py`) so a fresh clone gets the same
result as a configured machine.

## The gate

One command decides whether a change is mergeable:

```bash
make ci             # = make check + make check-sync + make check-frontend
```

- `make check` — `ruff format --check`, `ruff check`, `bandit`, `mypy`, and
  `pytest` with coverage, over `src/ tests/ scripts/`.
- `make check-sync` — fails if generated skill artifacts or adapter regions have
  drifted from their governed sources.
- `make check-frontend` — `tsc --noEmit` and a production build.

**CI never mutates the tree.** Run `make format` and `make fix` locally before
pushing; CI only verifies. If `make check-sync` fails, run `make sync-skills` —
never hand-edit a generated file.

Known gaps in what the gate covers, and other accepted debt, are listed in
[docs/known-limitations.md](docs/known-limitations.md). Add to it rather than
quietly working around an item.

## Generated files

Several tracked files are outputs, not sources. Editing them directly is always
wrong, because the next sync overwrites the edit:

| Generated | Source of truth |
|---|---|
| `CLAUDE.md`, `AGENTS.md`, `OPENCODE.md`, `.github/copilot-instructions.md` | `.github/skills/` + `adapters/templates/*.j2` |
| `.claude/skills/`, `.opencode/skills/`, `.codex/skills/`, `.agents/skills/` | `.github/skills/`, `.github/skills-external/` |
| `.claude/agents/`, `.opencode/agents/`, `.codex/agents/` | `.github/agents/` |
| `skills-lock.json`, `.agents/skills/.generated-manifest.tsv` | the sync engine |

Adding a new AI tool is one `[[tool]]` entry in
[adapters/registry.toml](adapters/registry.toml) plus a template — no Makefile
changes.

## Documentation is enforced

CI fails a pull request that touches `src/` or `tests/` without touching
`docs/`. This is deliberate. Update the relevant guide, or add an ADR under
[docs/adr/](docs/adr/) when the change encodes a decision someone will later ask
"why?" about.

## Conventions

- **English for every artifact** — code, comments, docstrings, documentation,
  commit messages, and all user-facing UI copy. This has drifted before.
- **Absolute imports only.**
- **Clean-architecture boundaries** as defined in
  [.github/domain-boundaries.md](.github/domain-boundaries.md) and
  [.github/architecture.md](.github/architecture.md). The API layer must not
  touch the Docker socket (see ADR-0002); worker-only concerns belong in
  `tasks.py`.
- **Prefer `make` targets and `uv`** over ad-hoc commands.

## Pull requests

1. Branch from `main`.
2. Make the change, with tests that would fail without it.
3. `make format && make fix && make ci` — all green.
4. Describe what you measured, not only what you changed. For anything touching
   run execution, provenance, or scoring, say which runs you used as evidence.

Changes to governance (`.github/`) affect every harness the lab generates, so
they get more scrutiny than application code. Say what you observed that
motivated the change — this repo has a whole loop
([docs/harness-lifecycle/](docs/harness-lifecycle/)) for turning run evidence
into governance improvements, and a proposal grounded in a run beats an opinion.

## Reporting problems

Bugs and feature requests go to GitHub issues; there is a template for each.
Security issues do not — report them privately, see [SECURITY.md](SECURITY.md).

## Code of conduct

Participation is covered by [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md). It is
short; read it once.

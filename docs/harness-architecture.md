# Harness Architecture

This repository is a polyvalent template: one governed source of truth in `.github/`
is projected into the native layout of every AI tool (Claude Code, OpenCode,
Antigravity, Codex, Copilot). This document explains the projection engine and how
to extend it.

## Layers

```
.github/                      Single source of truth
  architecture|standards|domain-boundaries|automation|orchestration.md   (L1/L3/L4)
  sdlc.md                     AI-assisted lifecycle with make gates        (L5)
  portability.md              Model tiers + self-hosted fallback (design)
  skills/                     Internal governed skills
  skills-external/            Synced external/vendor skills
  agents/                     Governed, tool-agnostic agent definitions
adapters/
  registry.toml               Declarative list of tools (the extension point)
  templates/*.j2              Jinja2 templates for adapter skill regions
src/ml_python_base/skills_sync/   The projection engine (one place, tested)
```

Generated, never hand-edited: `.claude/skills`, `.opencode/skills`, `.codex/skills`,
`.agents/skills` (+ manifest), `.claude/agents`, `.opencode/agents`, `.codex/agents`,
`skills-lock.json`, and the `<!-- BEGIN/END GENERATED SKILLS -->` region inside each
adapter file.

## The engine

`src/ml_python_base/skills_sync` (run via `uv run python -m ml_python_base.skills_sync`):

| Module | Responsibility |
|---|---|
| `config` | Load + validate `adapters/registry.toml` |
| `discovery` | Enumerate skills; apply internal-over-external precedence |
| `hashing` | Folder/file SHA-256 (byte-compatible with the legacy shell) |
| `linker` | Materialize native skill views (symlink / copy); prune stale |
| `manifest` | Antigravity `.generated-manifest.tsv` |
| `lockfile` | `skills-lock.json` (preserves timestamps when unchanged) |
| `ingest` | Promote ad-hoc external skills into the governed folder |
| `renderer` | Splice the managed skill region into adapter files (Jinja2) |
| `agents` | Discover governed agents; project to native agent formats |
| `cli` | `link` / `render` / `agents` / `ingest` / `sync` / `purge` / `check` |

## Adding a new AI tool

1. Append one `[[tool]]` entry to `adapters/registry.toml`:
   ```toml
   [[tool]]
   id = "cursor"
   display_name = "Cursor"
   native_skills_dir = ".cursor/skills"   # or "" if it reads .github/ directly
   link_strategy = "symlink"              # symlink | copy | none
   adapter_file = "CURSOR.md"
   adapter_template = "cursor.md.j2"
   ```
2. Add the template `adapters/templates/cursor.md.j2` (usually
   `{% include "_skills_block.j2" %}`).
3. Add the `<!-- BEGIN/END GENERATED SKILLS -->` sentinels to `CURSOR.md`.
4. Run `make sync-skills`. The native view and adapter region are generated. No
   Makefile change is required.
5. If `link_strategy` is not `none`, add the new `native_skills_dir` to
   `RUNNER_MASKED_SKILL_DIRS` in `src/harness_lab_app/executors.py`.

### Why step 5 exists

Each tool gets its own materialized catalog, which is right on your machine and
wrong inside a run: the runner bind-mounts the whole repository at `/app`, so the
agent discovers every catalog at once and loads the same governed skills two, three
or four times. The cost is not the duplicate-name warnings — 240 of them in run
`tetris-v1-K-064` — but that the catalog is part of the system prompt, re-sent and
re-prefilled on every turn of every step. Against a local model, four copies of
428 KB of `SKILL.md` is minutes per turn.

So a run sees exactly one catalog (`RUNNER_SKILLS_DIR`, `.claude/skills`) and the
others are hidden behind empty tmpfs mounts. Masking rather than deleting keeps the
host tree — the tools' source of truth, and what `make check-sync` gates — untouched
by a run. `tests/harness_lab_app/test_executors.py` fails if the mask list falls
behind the registry, which is what makes step 5 a reminder rather than a trap.

## Agents and the SDLC

- Define agents once in `.github/agents/*.md` (tool-agnostic frontmatter + prompt).
- `make sync-agents` projects them into each tool's native format (Claude subagents,
  OpenCode agents). Agents reference a **tier**, not a model id — see
  `.github/portability.md`.
- The lifecycle and its `make` exit gates live in `.github/sdlc.md`; the
  `orchestrator` agent drives it.

## Quality model

- CI is **read-only**: `make ci` = `make check` (ruff format --check, ruff check,
  bandit, mypy, pytest) + `make check-sync` (drift gate). Never relies on CI to
  format or fix.
- Run `make format` / `make fix` locally before pushing.
- Engine behavior is pinned by `tests/skills_sync/` — including a golden test that
  proves the Python folder-hash matches the original shell pipeline bit-for-bit.

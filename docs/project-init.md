# Project Initialization (`make init`)

How to turn a fresh clone of this template into a new, named project. The
AI-facing counterpart of this document is the internal skill
`bootstrap_project` (`.github/skills/bootstrap_project.md`), which guides any
supported AI tool through the same flow.

## Quickstart

```bash
git clone <your-clone-of-the-template> my_project && cd my_project
make init NAME=my_project
```

`make init` runs `scripts/init_project.py` and then chains:

1. `make install` — recreates `.venv` and regenerates `uv.lock` under the new
   package name (the lock file is never text-rewritten).
2. `make sync-skills` — rebuilds native skill/agent views and adapter regions,
   proving the renamed sync engine (`python -m <name>.skills_sync`) resolves.
3. `make template-remote-setup` — adds the `template` git remote so the
   project can keep receiving template updates (see `docs/template-sync.md`).
4. `make ci` — read-only quality gate (`make check` + `make check-sync`).

Preview everything without writing a byte:

```bash
python3 scripts/init_project.py --name my_project --dry-run
```

## What the script does

`scripts/init_project.py` is stdlib-only so it runs on a fresh clone before
any virtual environment exists. Steps:

- Validates the name (lowercase snake_case, not a Python keyword or reserved
  word) and derives the dist name (`my_project` → `my-project`).
- Refuses to run if the git tree is dirty (override with `--force`) or if
  `src/ml_python_base` no longer exists (already initialized).
- Purges stale `__pycache__` directories under `src/` and `tests/`.
- Renames `src/ml_python_base` → `src/<name>`.
- Rewrites `ml_python_base` / `ml-python-base` across an explicit allowlist
  (`pyproject.toml`, `Makefile`, `adapters/registry.toml`, docs, agents
  README, notebook kernel name, devcontainer name, and all Python sources).
- **Preserves every line containing the upstream template URL**
  (`git@github.com:...ml-python-base.git`): `TEMPLATE_REPO` in the `Makefile`
  and the example in `docs/template-sync.md` must keep pointing at the
  template or `make template-sync-merge` breaks.
- Creates `.env` from `.env.example` if absent. Review it afterwards — model
  endpoints default to localhost and are machine-specific. `.env` is
  gitignored; never commit it.

## After init

- Review the diff and commit the initialization as a single commit.
- Point `origin` at your new project's repository.
- Start work through the governed flow: `brainstorm_quick` (scoped ideation)
  or the external `brainstorming` skill (design-gated work), then
  `plan_and_execute_feature`. See `docs/skills-management.md` for the
  ideation routing table.

## Template-sync caveat after rename

Future `make template-sync-merge` runs pull upstream changes that still live
under `src/ml_python_base/`. Git's rename detection usually maps them onto
`src/<name>/`, but upstream edits to lines the rename rewrote (e.g. import
statements) will surface as merge conflicts — resolve them keeping your
package name.

## Known pre-existing cleanup (not handled by init)

The `run-api` / `run-question` / `run-interactive` Makefile targets reference
`api:app` and `main.py`, which this template does not ship; they are starting
points for projects that add an API/CLI entrypoint.

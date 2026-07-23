# Agentic Harness Lab

A local web app and toolkit for **benchmarking agentic coding harnesses** (Claude Code,
OpenCode, Codex, Antigravity, …) and models on real coding tasks — and feeding what you
learn back into the shared governance template (`ml-python-base`) as concrete skill
improvements.

Run agents on custom cases and SWE-bench / SWE-bench Pro issues in isolated Docker
containers, capture per-run provenance (condition hash, harness fingerprint, cost,
skills consulted), audit behaviour with an LLM, and compare harness/model conditions —
all from the web UI.

> **Get started:** `make app-up` → `http://localhost:5173`. New here? Read the
> **[Web UI guide](docs/guide-web-ui.md)**.
>
> **Improving the template from what you observe?** Follow the
> **[harness improvement loop](docs/harness-lifecycle/README.md)** — run a case → audit
> → generate a sanitized issue for `ml-python-base` → improve the skill → re-measure.

## 🚀 What it does

- **Benchmark lanes** — custom cases, SWE-bench, and SWE-bench Pro, one Docker
  container per run (Celery + Redis), with cancel/retry and orphan reconciliation.
- **Multiple harnesses** — Claude Code, OpenCode, Codex, and more, selectable per run.
- **Rich provenance** — condition hash, harness fingerprint, prompt/case hashes, cost,
  tokens, steps, diff, and skills consulted, captured per run.
- **Behavioural audit** — an LLM reads a run's artifacts and reports skill usage, loops,
  and SDLC adherence.
- **A/B comparison** — control vs harness-wrapped arms to isolate the harness's effect.
- **Improvement loop** — turn audit findings into sanitized issues for `ml-python-base`
  and re-measure (see the [harness improvement loop](docs/harness-lifecycle/README.md)).
- Built on **`uv` + `ruff` + `pytest`** with a read-only CI gate (`make ci`).

## 🔄 The improvement cycle

The lab runs its cases against a **specific version of the `ml-python-base`
governance** — the skills, agents, and rules the agent is given. That version is what
you improve. The full loop, tied to it:

1. **Run & observe.** Run a case in the UI (harness `claude`, a governance-loaded
   prompt variant like `swe_harness` / `sdlc`), let it finish, and open the run. The
   **Attribution** panel shows which skills were consulted; the **Audit** button
   writes a behavioural diagnosis. `make harness-status` shows which template version
   the lab is currently running.
2. **Generate the issue.** On the run, click **"Generar issue sanitizado"**. It shows
   a ready-to-paste, sanitized markdown issue (no private data). **Copy the whole block
   and open a new issue in
   [`ml-python-base`](https://github.com/marcosdh1987/ml-python-base)** — the first
   line is the title, the rest is the body.
3. **Improve & release.** In `ml-python-base`, improve the skill (your own agent/LLM),
   merge, and **cut a new release** (`vX.Y.Z`). Close the issue.
4. **Update the lab & validate.** Point the lab at the new version and **re-run the
   same case** to see whether it actually improved:

   ```bash
   make template-remote-setup             # first time only — see "Template governance"
   make harness-status                    # current version vs latest (vX.Y.Z)
   make harness-sync-preview REF=vX.Y.Z   # read-only diff of the new governance
   make harness-sync-branch  REF=vX.Y.Z   # adopt it in an isolated candidate worktree
   #   light alternative: make template-sync REF=vX.Y.Z
   ```

   Then re-run the case (same harness/model/prompt) and compare its **Attribution** +
   **Audit** against the earlier run: did the symptom improve? Did the success
   criterion hold? If yes, the new version is validated; if not, iterate again.

Deliberately light and human-driven — full playbook in
**[docs/harness-lifecycle/improvement-methodology.md](docs/harness-lifecycle/improvement-methodology.md)**.
For the rigorous, fully-traceable form (immutable releases + the canonical regression
suite with hard gates), see the
[harness lifecycle docs](docs/harness-lifecycle/README.md).

## 📋 Prerequisites

- **Docker Desktop** — required; the web app and every agent run in containers.
- **Make** and **git**.
- [uv](https://github.com/astral-sh/uv) — Python 3.11+ package manager (only needed to
  develop the lab itself; the app stack runs in Docker).
- An agent credential and an LLM gateway token (see **Setup** below).

### Install uv

```bash
# macOS/Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Or with pip
pip install uv
```

## 🛠️ Quick Start

### 1. Setup (first time, per machine)

```bash
git clone https://github.com/marcosdh1987/ai-agentic-harness-lab.git
cd ai-agentic-harness-lab
cp .env.example .env
```

Then edit `.env` and set at least:

- **`CLAUDE_CODE_OAUTH_TOKEN`** (from `claude setup-token`) **or `ANTHROPIC_API_KEY`** —
  so the `claude` harness can authenticate inside the runner container.
- **`GATEWAY_TOKEN`** (+ `GATEWAY_BASE_URL`) — the LLM gateway used by the behavioural
  audit and issue synthesis (and by gateway-routed models).

Finally, point the lab at the template (once) so you can pull governance versions:

```bash
make template-remote-setup    # see "Template governance" below
```

### 2. Run the lab (web app)

```bash
make app-up          # builds the runner image + starts api/worker/redis/frontend
```

Open **http://localhost:5173**. New here? Read the
**[Web UI guide](docs/guide-web-ui.md)**. Claude/OpenCode/Codex all run inside the
runner container using the auth from `.env`; see
[docs/harness-adapters.md](docs/harness-adapters.md).

### Develop the lab (optional — only to work on the lab itself)

```bash
make install                 # pin Python, create .venv, sync from uv.lock
source .venv/bin/activate
make setup-hooks             # pre-commit hooks (optional, recommended)
make ci                      # read-only quality gate (ruff, mypy, bandit, pytest)
```

### 🐳 Dev Containers (Recommended)

This project is configured to run inside a **Dev Container**. This guarantees that you are working in the exact same environment as production (Linux), regardless of your local OS (macOS, Windows).

### How to use

1. Install **Docker Desktop**.
2. Install the **Dev Containers** extension in VS Code.
3. Open the project in VS Code.
4. Click on the pop-up **"Reopen in Container"** (or run the command from the Palette).

### Benefits

- **Zero Setup**: The container installs Python, `uv`, and all dependencies automatically.
- **Production Parity**: Develop on Linux, deploy on Linux.
- **Jupyter Integration**: Notebooks run seamlessly inside the container, using the container's kernel.


## 📦 Managing Dependencies

We use `uv` to manage dependencies in `pyproject.toml` and lock them in `uv.lock`.

### Add a new library

Instead of editing files manually, use the helper command:

```bash
# Add a package (e.g., tensorflow)
make add PKG=tensorflow

# Add a development dependency
make add PKG="pytest --dev"
```

This will:
1. Add the package to `pyproject.toml`
2. Update `uv.lock`
3. Install the package in your environment

### Remove a library

To remove a package you no longer need:

```bash
make remove PKG=tensorflow
```

### Generate requirements.txt

If you need a `requirements.txt` for legacy systems or deployment:

```bash
make generate-requirements
```

## 🎯 Code Quality

The project provides three main levels of code quality checks:

1. **`make fix` (Recommended)**: The "do it all" command. It auto-formats code, sorts imports, removes unused imports, fixes linting issues, and cleans Jupyter notebooks. Run this frequently!
2. **`make fix-force`**: Same as `fix`, but applies "unsafe" fixes. Use with caution (e.g., it might remove imports used only in `try/except` blocks).
3. **`make lint`**: Runs strict static analysis and security checks (Bandit). It does not modify files. Use this to verify your code before pushing.
4. **`make format`**: A lighter version of `fix`. Only formats code and sorts imports.

```bash
# 1. Clean everything (Safe mode)
make fix

# 2. Clean everything (Aggressive mode - check changes after!)
make fix-force

# 3. Verify quality and security (Read-only check)
make lint

# 4. Run full CI pipeline (Fix + Lint + Test)
make ci
```

## 📁 Project Structure

```
.
├── src/harness_lab_app/    # Backend: FastAPI API, Celery tasks, services, harness_lifecycle
├── frontend/               # React/TypeScript web UI
├── benchmarks/             # Cases + the canonical regression suite (harness-regression-v1)
├── scripts/                # bench_run, bench_audit, harness_lifecycle CLI, …
├── .github/                # Governed skills, agents, and governance docs (from the template)
├── data/runs/              # Per-run artifacts (git-ignored)
├── data/harness-proposals/ # Reviewed HEP proposals (tracked)
├── docs/                   # Guides, ADRs, and the harness lifecycle docs
├── Makefile                # Commands
├── pyproject.toml          # Config & dependencies
├── uv.lock                 # Exact versions lockfile (DO NOT EDIT MANUALLY)
└── .harness-version.yaml   # The adopted ml-python-base governance version (once set)
```

## 🧭 AI Rules Structure (Cross-Tool)

The lab's governance — inherited from `ml-python-base` — uses a consistent four-level
strategy that works across Claude Code, OpenCode, Codex, Antigravity, and Copilot. It
is exactly the harness the lab benchmarks and improves.

### Level 1 — Governance

- `.github/architecture.md`
- `.github/standards.md`
- `.github/domain-boundaries.md`

### Level 2 — Operational Skills

Governed skills live in `.github/skills/` (internal, the source of truth) and
`.github/skills-external/` (vendored). They are projected into each tool's native
layout (`.claude/skills/`, `.opencode/skills/`, `.agents/skills/`) by the sync engine:

```bash
make sync-skills             # refresh native skill/agent views + skills-lock.json
make check-sync              # fail if any generated skill artifact is stale
```

See `.github/skills/README.md` for the current catalog. Improvements to these skills
flow back to the template via the
[harness improvement loop](docs/harness-lifecycle/README.md).

### Level 3 — Automation

- `.github/automation.md`
- CI and local checks through `make lint`, `make test`, and `make ci`
- On PRs, if `src/` or `tests/` changes, at least one file in `docs/` must be updated
- Test flow enforces `make format` and `make fix` before running tests

### Level 4 — Orchestration

- `.github/orchestration.md`
- Plan-first requirement
- Step-by-step execution
- Mandatory diff review
- Validation against automation
- No direct large generation without skill invocation

Adapters:

- Claude Code entrypoint: `CLAUDE.md`
- Claude Code native skills: `.claude/skills/` generated from `.github/skills/` and `.github/skills-external/`
- Copilot entrypoint: `.github/copilot-instructions.md`
- Antigravity workspace rules: `.agents/rules/`
- Antigravity native skills: `.agents/skills/` generated from `.github/skills/` and `.github/skills-external/`

Documentation template:

- `docs/implementation-template.md` (use it when implementing and testing new changes)

Self-hosted / local-model mode (`local_model_32k`):

- To develop with small self-hosted models (Qwen 3.x / 3.6, 27B-35B @ 32k), the recommended
  path is **OpenCode routed through your ai-gateway** (`make opencode`): plan with a strong
  cloud model, build local at $0, all visible in Langfuse. Start with the **"Priority order"**
  in [docs/local-model-runtime-config.md](docs/local-model-runtime-config.md) — the mechanical
  layer (gateway output cap, repetition penalty, served context) that makes a 32k model
  usable. Short operating rules: `LOCAL_AGENT.md`. Sizing heuristics:
  [docs/task-sizing.md](docs/task-sizing.md).

## 🔁 Agentic Working Loop

Beyond the cross-tool rules above, this template ships an opinionated **working loop**
for AI-assisted development — optimized for Claude Code — so the high-quality path is
the path of least resistance:

**Ground → Plan → Delegate → Verify → Compound.**

| Piece | Where | Purpose |
|-------|-------|---------|
| Operating playbook | `CLAUDE.md` → *Operating playbook* | The loop as standing instructions for the agent. |
| Slash commands | `.claude/commands/` | `/plan`, `/orchestrate`, `/verify`, `/adr`, `/retro`. |
| Loop skills | `.github/skills/` | `systematic_debugging`, `verify_changes`, `retrospective` (+ existing planning/review skills). |
| Hooks (nudge) | `.claude/settings.json`, `.claude/hooks/` | Inject the loop at session start; remind to verify/compound when code changed. Non-blocking. |
| Memory | `memory/` | Persistent working memory (`context`, `learnings`, `patterns`) across sessions. |
| Decisions | `docs/adr/` | Architecture Decision Records — the durable "why". |
| MCP servers | `.mcp.json` | Optional extra tools (library docs via context7, git). Opt-in; require the tool to be installed. |

Full guide: [docs/agentic-workflow.md](docs/agentic-workflow.md). How this maps to
assessments of AI-assisted coding skill (and what a template can/can't influence):
[docs/agentic-scoring.md](docs/agentic-scoring.md).

## 🔧 Available Commands

| Command | Description |
|---------|-------------|
| `make install` | Setup environment, install python version and sync dependencies |
| `make add PKG=x` | Add a new dependency to the project |
| `make remove PKG=x` | Remove a dependency from the project |
| `make setup-hooks` | Install pre-commit hooks for git |
| `make format` | Format code with Ruff |
| `make lint` | Run code quality checks |
| `make fix` | Auto-fix linting issues |
| `make test` | Run tests with coverage |
| `make ci` | Run full CI pipeline |
| `make setup-claude-skills` | Generate `.claude/skills` native symlinks from governed skills |
| `make setup-antigravity-skills` | Generate `.agents/skills` native Antigravity mirror from governed skills |
| `make sync-skills` | Sync external skills, refresh `skills-lock.json`, and refresh Claude and Antigravity native skill layouts |
| `make purge-external-skills` | Remove all external skills and refresh Claude and Antigravity native skill layouts |
| `make app-up` | Start the web app in Docker (api + worker + redis + frontend) |
| `make harness-status` | Installed template version vs latest release + drift |
| `make harness-sync-preview REF=vX.Y.Z` | Read-only diff of a candidate template release |
| `make harness-sync-branch REF=vX.Y.Z` | Prepare a candidate in an isolated worktree |
| `make harness-proposal RUN=<id>` | Draft a sanitized improvement issue for the template |
| `make template-remote-setup` | Add or update the `template` upstream remote |
| `make template-sync REF=vX.Y.Z` | Legacy selective governance sync from a tag |
| `make generate-requirements` | Export `uv.lock` to `requirements.txt` |
| `make clean` | Remove cache and generated files |
| `make help` | Show all available commands |

## Template governance

The lab's governance layer — skills, agents, governance docs, and adapter templates —
comes from the [`ml-python-base`](https://github.com/marcosdh1987/ml-python-base)
template. The lab **consumes** it as an immutable release; it does not publish it.

### First-time setup (once)

Point the lab at the template repository — this adds a git remote named `template`:

```bash
make template-remote-setup
# adds the `template` remote → git@github.com:marcosdh1987/ml-python-base.git
```

Using a fork or HTTPS? Override the URL:

```bash
make template-remote-setup TEMPLATE_REPO=https://github.com/<you>/ml-python-base.git
```

### Everyday use

```bash
make harness-status                   # installed version vs latest release + drift
make harness-sync-preview REF=vX.Y.Z  # read-only diff of a candidate release
make harness-sync-branch  REF=vX.Y.Z  # prepare the candidate in an isolated worktree
```

Full flow (installed state, isolated candidate worktree, adoption, compatibility): see
**[docs/harness-lifecycle/lab-sync-workflow.md](docs/harness-lifecycle/lab-sync-workflow.md)**.
The lighter legacy selective sync — `make template-sync REF=vX.Y.Z` (governance layer
only; never touches `src/`, `Makefile`, `data/`, or the sync engine) — also works, and
records the adopted version in `.template-version` so `make harness-status` can show it.

To feed improvements *back* to the template from what the lab observes, follow the
**[harness improvement loop](docs/harness-lifecycle/README.md)**.

## 🐳 Docker

The web app runs entirely in Docker via `make app-up` (api + worker + redis + frontend,
plus a per-run runner container). Agents — including `claude` — execute inside the
runner image with auth supplied from `.env` (`CLAUDE_CODE_OAUTH_TOKEN` / `ANTHROPIC_API_KEY`).

```bash
make app-up          # build the runner image + start the full stack
make stop-docker     # stop containers
```

## 📝 Configuration

- **Dependencies**: `pyproject.toml` - `[project.dependencies]`
- **Ruff**: `pyproject.toml` - `[tool.ruff]`
- **Pytest**: `pyproject.toml` - `[tool.pytest.ini_options]`
- **Editor**: `.editorconfig`

## 🤝 Contributing

1. Create a new branch
2. Make your changes
3. Run `make ci` to ensure quality
4. Submit a pull request

## 📄 License

This is a template project - customize as needed for your use case.

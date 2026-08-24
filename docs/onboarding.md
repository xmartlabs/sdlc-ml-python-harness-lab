# Onboarding — run the lab for the first time

Step-by-step setup for a developer cloning the lab on a **new machine**. At the end
you will have the web app running, one benchmark run executed, and the improvement
loop within reach.

Already set up? Jump to [Operating the lab](#7-what-next--operating-the-lab).

---

## 1. Prerequisites

| Tool | Why | Check |
|---|---|---|
| **Docker Desktop** | The web app and every agent run in containers | `docker info` |
| **make** + **git** | All workflows are Makefile-driven | `make --version` |
| [uv](https://github.com/astral-sh/uv) *(optional)* | Only to develop the lab itself (tests, lint) | `uv --version` |
| **Node 20+** *(optional)* | Only for host-mode frontend development | `node --version` |

```bash
git clone https://github.com/marcosdh1987/ai-agentic-harness-lab.git
cd ai-agentic-harness-lab
cp .env.example .env
```

Everything below is about filling in `.env` (it is gitignored — keys never get
committed) and starting the stack.

## 2. Choose your credential path

The lab has **two independent credential surfaces**. You can start with either, but
most features light up with both:

```mermaid
flowchart TD
    Q{"What do you have?"} -->|"A LiteLLM / AI-gateway endpoint"| A["Path A — AI gateway<br/>GATEWAY_BASE_URL + GATEWAY_TOKEN<br/>+ LITELLM_MASTER_KEY"]
    Q -->|"A Claude account (subscription or API key)"| B["Path B — Claude native<br/>CLAUDE_CODE_OAUTH_TOKEN<br/>or ANTHROPIC_API_KEY"]
    Q -->|"Both"| AB["Do A + B — full functionality"]
    A --> FA["Enables: opencode harness (gateway models),<br/>LLM audit, improvement synthesis,<br/>SWE auto-score judge, Langfuse attribution"]
    B --> FB["Enables: claude harness<br/>(Claude Code CLI inside the runner container)"]
```

| Feature | Needs Path A (gateway) | Needs Path B (Claude) |
|---|---|---|
| `opencode` harness runs (gateway-routed models, incl. self-hosted) | ✅ | — |
| `claude` harness runs | — | ✅ |
| Behavioural **audit** ("Run audit" button) | ✅ | — |
| **Improvement proposals** / sanitized issue synthesis | ✅ | — |
| SWE-bench Pro **auto-scoring** (LLM judge) | ✅ | — |
| Per-run cost/attribution in Langfuse | ✅ | — |

> **TL;DR:** with only Path B you can benchmark the `claude` harness, but the
> audit → improvement loop needs the gateway. Set up **Path A first** if you can.

### Path A — AI gateway (LiteLLM) — recommended

The lab expects an OpenAI-compatible gateway (LiteLLM is the tested one). It routes
model calls for the `opencode` harness, the audit/improvement LLMs, and creates
**per-run virtual keys** so every run is attributable in Langfuse.

In `.env`:

```bash
# Your gateway endpoint — MUST end in /v1
GATEWAY_BASE_URL=http://localhost:4000/v1
# The LiteLLM master key (or a virtual key with model access)
GATEWAY_TOKEN=sk-...
# Master key again — used to mint per-run virtual keys with session tags
LITELLM_MASTER_KEY=sk-...
```

Optional model overrides (LiteLLM model names, no `gateway/` prefix):

```bash
# AUDITOR_MODEL=gpt-5.4-mini       # behavioural audits (default gpt-5.5)
# IMPROVE_MODEL=gpt-5.4-mini       # improvement-issue synthesis (defaults to AUDITOR_MODEL)
# CASE_SUMMARY_MODEL=gpt-5.4-nano  # SWE-bench Pro import polish (nano-tier is plenty)
```

Then pull the gateway's live model list into the lab:

```bash
make opencode-doctor       # sanity: gateway + local endpoints reachable
make sync-gateway-models   # curate the live list into the tracked opencode.json
```

Once the app is up you never need the CLI for this again: **Settings → Refresh
from gateway** adopts whatever LiteLLM serves and makes it routable immediately,
without touching `opencode.json`
([ADR-0016](adr/0016-opencode-config-overlay-for-model-routing.md)). The same page
edits the gateway URL, tokens and model defaults in `.env` — atomically, with
rolling backups, and with no restart needed
([ADR-0014](adr/0014-web-editable-env-allowlist.md)). `make sync-gateway-models`
remains the way to *promote* a model you have decided to keep into the tracked
map, so the CLI lane and git history see it too.

Skills work the same way. **Skills → Import a skill** pulls one from a GitHub URL
(repo, subdirectory or `blob` link), a raw `SKILL.md`, or an uploaded
`.md`/`.zip`/`.tar.gz`. You get a preview first — file list, detected name,
warnings — and nothing is written until you confirm. Multi-file skills keep their
`scripts/` and `references/`, and every file is editable from the same page. The
tree lives in the database and is materialized to the git-ignored
`data/skills_staging/`, so trialling a skill never dirties the working tree
([ADR-0017](adr/0017-multi-file-staged-skills-and-remote-import.md)). Promote when
it earns its keep; the A/B workflow is in
[`docs/guide-skill-validation.md`](guide-skill-validation.md).

Don't have a gateway yet? A minimal LiteLLM setup is one container + one YAML —
see the [LiteLLM docs](https://docs.litellm.ai/docs/proxy/quick_start). Point it
at any provider (OpenAI, Anthropic, Bedrock, your LM Studio/Ollama servers) and
the lab consumes them uniformly as `gateway/<model>`.

### Path B — Claude Code with your own account

The `claude` harness runs the Claude Code CLI **inside the runner container**; it
needs credentials via `.env`. Use **either**:

**Option 1 (recommended) — your Claude subscription.** On your **host** terminal:

```bash
# Install the Claude Code CLI if you don't have it yet:
npm install -g @anthropic-ai/claude-code

# Generate a long-lived token (opens a browser to authorize with your account):
claude setup-token
```

Copy the token it prints (starts with `sk-ant-oat...`) and paste it into `.env`:

```bash
CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-...
```

**Option 2 — pay-as-you-go Anthropic API key** (from
[console.anthropic.com](https://console.anthropic.com/settings/keys)):

```bash
ANTHROPIC_API_KEY=sk-ant-...
```

Restart the stack after editing `.env` (`make stop-docker && make app-up`) — the
token flows into the runner container from there. No gateway involvement for the
runs themselves. Design details: [harness-adapters.md](harness-adapters.md).

### Path C — other harnesses and backends

| You want | Path | Guide |
|---|---|---|
| **codex** harness | Host mode (`make app-host`) — codex drives your host CLI's native OpenAI login; not wired in Docker yet | [runbook-claude-codex.md](runbook-claude-codex.md) |
| **Self-hosted models** (LM Studio / Ollama) | Route them through your LiteLLM gateway (recommended: build profile + Langfuse visibility) or point `OLLAMA_BASE_URL` / `LMSTUDIO_BASE_URL` directly | [local-model-runtime-config.md](local-model-runtime-config.md) — start with "Priority order"; sizing: [task-sizing.md](task-sizing.md) |
| **LM Studio / Ollama, no gateway** (fastest way to compare models) | Set `LMSTUDIO_BASE_URL` and/or `OLLAMA_BASE_URL` to those hosts **including `/v1`** on Settings → Local runtimes. The run picker's *Direct (local)* source then lists what each host is really serving, with its context window. Ollama also needs `OLLAMA_CONTEXT_LENGTH=32768` on the server — its 4096 default is too small for the harness | [local-model-runtime-config.md](local-model-runtime-config.md#the-direct-lanes-skip-the-gateway-when-you-are-iterating) |
| **antigravity** | Human-driven flow (not executed by the runner) | [harness-adapters.md](harness-adapters.md) |

## 3. Point the lab at the governance template (once)

The lab benchmarks and improves the [`ml-python-base`](https://github.com/marcosdh1987/ml-python-base)
governance (skills, agents, rules). Add it as a git remote so you can pull and
compare versions:

```bash
make template-remote-setup
# fork or HTTPS instead:
make template-remote-setup TEMPLATE_REPO=https://github.com/<you>/ml-python-base.git

make harness-status   # confirms: installed governance version vs latest release
```

### If you forked the template

The lab files improvement issues against GitHub slugs that default to the upstream
repos. If you work from forks, retarget them in `.env` — no code change:

| Variable | Default | What it is |
|---|---|---|
| `HARNESS_LAB_TEMPLATE_REPOSITORY` | `marcosdh1987/ml-python-base` | Where improvement issues are filed |
| `HARNESS_LAB_LAB_REPOSITORY` | `marcosdh1987/ai-agentic-harness-lab` | Recorded as the source of a proposal's evidence |
| `HARNESS_LAB_GUIDE_REPOSITORY` | `marcosdh1987/harness-engineering-guide` | Target for harness-methodology findings |

These are runtime defaults only. A proposal already stored keeps the slug it was
authored against, so retargeting never rewrites history.

`make template-remote-setup TEMPLATE_REPO=…` sets the *git remote*; these set the
*issue targets*. Both usually point at the same fork.

## 4. Start the lab

```bash
make app-up
```

This builds the per-run runner image and starts four services:

| Service | Where |
|---|---|
| Frontend (web UI) | http://localhost:5173 |
| API (FastAPI) | http://localhost:8009 |
| Celery worker + Redis | internal |
| Runner | one container **per run**, created on demand |

Stop everything with `make stop-docker`. (For a server that stays on, use
`make app-up-prod` instead — one origin on `:8080`; see below.)

> **Reaching the UI from another machine.** The API is published on `127.0.0.1`
> by default. Authentication is only enforced once an admin password exists, so
> binding to every interface before that leaves an unauthenticated
> code-execution API on your network. Set the password first, then opt in:
>
> ```bash
> make set-password
> HARNESS_LAB_API_BIND_ADDR=0.0.0.0 make app-up
> ```
>
> See [SECURITY.md](../SECURITY.md) for what a run container can actually do.

> **Host mode (alternative):** `make app-host` runs the worker/API on your host so
> harnesses reuse your host CLI sessions (needed for codex). See the comments above
> `app-host` in the `Makefile`.

### Protect the UI if the lab is not on your laptop

The app ships **open**: no password, and the ports publish on every interface. That
is fine while it runs on your machine and wrong the moment it lives on a box that
stays on — the UI creates containers, edits `.env` and reads your credentials.

```bash
make set-password        # prompts twice; writes a hash + session secret to .env
make set-password OFF=1  # back to open mode
```

It applies to the next request — no restart. Until you run it, the UI carries a
banner saying authentication is off. One password, one operator, no account
recovery: forget it and you run the command again on the server. The threat model
is **LAN-only** and assumes plain HTTP — see
[ADR-0015](adr/0015-single-user-session-auth.md) before exposing the lab any wider.

### Leave it running — the always-on stack

`make app-up` is the development stack: Vite with hot reload, the API published on
every interface. For a box that stays on, use the production profile instead
([ADR-0019](adr/0019-always-on-local-deployment.md)):

```bash
make app-up-prod    # SPA baked into nginx on :8080; API only on loopback
make app-down-prod
make backup         # snapshot .env + database + run artifacts
```

Everything restarts with the host and rotates its logs, so an unattended lab does not
quietly fill the disk. The UI binds to `127.0.0.1` until you say otherwise — reach it
from another machine by setting this in `.env`, **after** `make set-password`:

```bash
HARNESS_LAB_BIND_ADDR=0.0.0.0
```

To bring the stack back after a reboot, install the launchd agent (macOS; the
systemd equivalent is in the comments of `deploy/harness-lab-boot.sh`):

```bash
sed "s|__REPO_ROOT__|$PWD|g" deploy/harness-lab.plist \
  > ~/Library/LaunchAgents/com.harnesslab.stack.plist
launchctl load ~/Library/LaunchAgents/com.harnesslab.stack.plist
```

An always-on lab also accumulates database. The artifact index is rebuilt from
disk on demand, so it is safe to compact:

```bash
make app-db-compact DRY=1   # report what would go
make app-db-compact         # delete stale rows, then VACUUM
```

It drops index rows the indexer no longer writes — `workspace/` trees from before
that exclusion existed, and runs deleted from the UI — and returns the freed pages
to the filesystem. On the reference laptop this took the database from 595 MB to
115 MB; no run, score or artifact on disk is touched. The database itself runs in
WAL mode, so the UI reads a consistent snapshot while a benchmark writes its log.

One thing does not travel: the in-platform **sandboxes** (web terminal / noVNC
desktop) hand the browser a `localhost` URL, so they only work from the server itself.
Runs, scoring and comparison are unaffected.

## 5. Smoke test — your first run

1. Open **http://localhost:5173** → **Runs** → **New run**.
2. Pick a simple case (e.g. `tetris-v1`), a **harness** you configured
   (`claude` for Path B, `opencode` for Path A), a governance-loaded **prompt
   variant** (`sdlc_full` / `swe_harness`), and an **executor model**
   (gateway models come live from LiteLLM; if one is marked *not routable*,
   enable it in **Settings → Model catalog** first).
3. Create the run and watch the logs stream. When it finishes:
   - **SWE-bench (Pro)** cases auto-score; custom cases wait for your manual score
     (state `awaiting_manual_score` — submit it in the run detail).
4. In the run detail, click **Run audit** (needs Path A) — the LLM reads the run's
   artifacts and reports skill usage, loops, and SDLC adherence.

If all of that worked, your machine is fully onboarded.

**Something failed?**

| Symptom | Check |
|---|---|
| Model dropdown empty | Settings → **Test connection**; `GATEWAY_BASE_URL` ends in `/v1`; gateway reachable from Docker (use `host.docker.internal`, not `localhost`, when the gateway runs on your host) |
| Run rejected with "not routable" | Settings → **Refresh from gateway**, then enable the model — the gateway serves it but nothing can route it yet |
| `claude` run fails auth | `CLAUDE_CODE_OAUTH_TOKEN` (or `ANTHROPIC_API_KEY`) set in `.env` **before** `make app-up`; restart the stack after editing `.env` |
| Audit button errors with gateway config | Set `GATEWAY_TOKEN` + `GATEWAY_BASE_URL` on the Settings page — they take effect on the next request, no restart |
| Runs stuck in `pending` | Open **Health** — it says whether a worker is replying at all; then worker logs: `docker compose logs -f worker` |
| `git status` dirty after a run, or `make check-sync` failing | A run died mid-ablation. **Health → Re-sync skill views** restores the generated views ([ADR-0018](adr/0018-health-and-drift-panel.md)) |
| Skill import refused with "non-public address" | The URL resolves to a private/loopback host. Imports are https-only and must reach a public address (ADR-0017) — download it and use **Upload** instead |
| A staged skill's files vanished from `data/skills_staging/` | The database still has them — the directory is a materialization; rebuild it from the Skills page rather than re-importing |
| Every page shows "Authentication required" | The session expired (7 days) or the secret was rotated — sign in again; `make set-password` if you lost the password |
| Every step logs `stream error` (gateway model) | The self-hosted server behind that model (LM Studio / Ollama / NIM host) is off or unreachable from the gateway — start it and re-run. Run creation preflights this and rejects with a clear error when the backend is provably down (skip with `HARNESS_LAB_DISABLE_GATEWAY_PROBE=1`) |
| No run attribution in Langfuse | `LITELLM_MASTER_KEY` matches your gateway's master key |

## 6. Optional — develop the lab itself

```bash
make install       # pin Python, create .venv, sync deps (uv)
make setup-hooks   # pre-commit hooks
make ci            # read-only quality gate: ruff, mypy, bandit, pytest
cd frontend && npm install && npx tsc --noEmit   # frontend typecheck
```

A Dev Container config is included (VS Code → "Reopen in Container") for a
zero-setup Linux environment.

## 7. What next — operating the lab

Once set up, your day-to-day is the **improvement cycle**: run → audit → propose →
file an issue → improve the template → re-measure.

| I want to… | Read |
|---|---|
| Understand every screen of the web UI | [guide-web-ui.md](guide-web-ui.md) |
| Run the audit → proposals → GitHub-issue loop | [improvement-cycle.md](improvement-cycle.md) |
| The methodology behind the loop (when to trust a proposal) | [harness-lifecycle/improvement-methodology.md](harness-lifecycle/improvement-methodology.md) |
| Consolidate proposals from several runs into ONE issue | Improvements menu in the UI (saved-proposals worklist) — see [guide-web-ui.md](guide-web-ui.md) |
| Adopt a new template release and validate it | [harness-lifecycle/lab-sync-workflow.md](harness-lifecycle/lab-sync-workflow.md) |
| Benchmark with SWE-bench / SWE-bench Pro | [design-swe-bench-cases.md](design-swe-bench-cases.md), [design-swe-bench-pro-lane.md](design-swe-bench-pro-lane.md) |
| Compare harnesses (claude vs codex vs opencode) | [harness-adapters.md](harness-adapters.md), [runbook-claude-codex.md](runbook-claude-codex.md) |
| Tune self-hosted/local models so they converge | [local-model-runtime-config.md](local-model-runtime-config.md), [task-sizing.md](task-sizing.md) |

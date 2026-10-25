# Harness adapters — opencode, codex, claude

> **Just want to run tests with claude/codex?** See the step-by-step
> [runbook-claude-codex.md](runbook-claude-codex.md). This page is the design
> reference behind it.

The lab treats the **harness** (the agent CLI that drives a run) as an
independent variable. Three are wired today:

| Harness | CLI | Runs in | Auth | Model selection |
|---|---|---|---|---|
| `opencode` | `opencode run` | runner **container** | LiteLLM gateway (`gateway/…`) | live gateway list |
| `codex` | `codex --quiet --full-auto` | your **host** | native (OpenAI, your `codex` login) | native menu → `-m` |
| `claude` | `claude -p` | **container** (Docker) or **host** | native (Anthropic) — env token in Docker, your login on host | native menu → `--model` |

(`antigravity` remains a separate human-driven flow, not executed by the runner.)

The command builders live in `scripts/bench_run.py`
(`build_opencode_cmd` / `build_codex_cmd` / `build_claude_cmd`, dispatched by
`build_harness_cmd`). The workspace path (`workspace/<run_id>/`) is carried in
the **prompt**, so all three run with `cwd=REPO_ROOT` and produce the same
artifact — only the CLI differs. Pick the harness in the New evaluation form (or
the single-run form under Advanced → Runs).

## Run `claude` in Docker (whole lab in `make app-up`)

You don't have to use host mode for claude — the Claude Code CLI is baked into the
runner image, so it runs in the same Docker lane as opencode/SWE-bench. It just
needs credentials, supplied once via `.env` (which flows into the runner
container):

1. **Subscription (recommended):** on your host, run `claude setup-token` (while
   logged in), and paste the long-lived token into `.env`:
   ```
   CLAUDE_CODE_OAUTH_TOKEN=<token from setup-token>
   ```
   This uses your Claude plan. Or use an API key instead: `ANTHROPIC_API_KEY=sk-ant-…`.
2. Rebuild the runner image so it has the CLI: `docker compose build runner`
   (or just `make app-up`, which builds it).
3. Create a claude run as usual — no host worker needed.

**The CLI version is pinned** (`ARG CLAUDE_CODE_VERSION` in `Dockerfile.runner`) and
recorded as the image label `claude-code.version`. Newer models need a newer CLI —
the 5.5 family and Fable 5.1 need 2.1.280 or later (`MIN_CLAUDE_CLI_VERSION` in
`native_models.py`) — and an unpinned install stays on whatever Docker cached. Bump
the ARG and rebuild when a model needs it. **Health** shows the image's version and
warns when it is too old for a current model, and preflight refuses such a run
instead of letting it die on the API's `claude_code_version_too_old`.

In Docker every claude run reaches Anthropic through the gateway
(`ANTHROPIC_BASE_URL` in `.claude/settings.local.json`), so the gateway must have a
card for the model too; preflight checks that as well.

### Claude observability + A/B hygiene (stream-json)

`claude` runs execute with `--output-format stream-json --verbose`: the raw
events (one JSON per assistant message / tool call / result) are teed to
`steps/step-N.events.jsonl`, while the step log receives a human-readable
rendering (`→ Read(path)`, `→ Bash(cmd)`, final stats line). Three consumers:

- **Attribution** (`attribution.build_attribution_from_events`): first-class
  tool calls — files read/edited, bash histogram, governance coverage, and
  *real* skill invocations (the `Skill` tool), not just SKILL.md reads.
- **usage.json** (`bench_run.collect_claude_usage`): tokens, cost, turns,
  duration and modified files summed from the per-step `result` events.
- **Audit**: step logs now show the tool timeline, so audits stop being
  narrative-only.

**Control-arm hygiene:** Claude Code auto-loads `CLAUDE.md` + `.claude/skills/`
from its cwd, so a "bare" run launched from the repo root silently inherits the
whole lab harness. For SWE cases with the `swe_default` variant, the claude
agent therefore runs with **cwd = workspace/<run_id>/**; the `swe_harness`
variant keeps the repo-root cwd so the surface is available. Prompts adjust
their path references accordingly.

**A different cwd is not isolation on its own.** The workspace lives *under* the
repo root, which is bind-mounted whole at `/app`. Two archived `native` runs show
what that means in practice: the agent got none of the lab's `.claude/` (no
subagents, no slash commands, no skills, no SessionStart hook) — but it did get
`/app/.mcp.json`, and Claude Code's memory lookup collects `CLAUDE.md` from every
ancestor directory. So the container masks the governance surface for every
condition that runs from the workspace (ADR-0049):

| `harness_source` | agent cwd | what the container masks |
|---|---|---|
| `lab_root` | the lab repo | nothing — the lab's harness *is* the treatment |
| `native` / `injected` | `workspace/<run>/` | `.claude`, `.agents`, `.opencode`, `.codex`, `memory`, `docs/adr`, and the adapter files (`CLAUDE.md`, `AGENTS.md`, `OPENCODE.md`, …) plus `.mcp.json`. `.github/` stays readable: `bench_run.py` projects the injected harness from it |
| `bare` | `workspace/<run>/` (empty) | all of the above **and** `.github/` |

`opencode.json` is never masked — it is routing, not governance, and blanking it
would take the gateway provider block with it. The `swe_default` cwd hygiene
above predates this and is unchanged.

Two details the docker lane handles for you:

- **Root + skip-permissions:** the runner container runs as root, and Claude
  Code refuses `--dangerously-skip-permissions` as root unless `IS_SANDBOX=1`
  is set. `build_run_environment` injects it into every run container (the
  same escape hatch Anthropic's devcontainers use).
- **Project settings reach the run as environment, not as a file.** Claude Code
  reads `.claude/settings*.json` from its *project* directory, so a run whose cwd
  is the workspace never saw the lab's — and with it `ANTHROPIC_BASE_URL`, i.e.
  the LiteLLM gateway and its metrics headers. `build_run_environment` therefore
  merges the `env` block of
  `.claude/settings.json` and `.claude/settings.local.json` (local wins, and both
  win over `.env`) into every run container, so routing is identical whatever the
  harness condition. The file is still gitignored — copy it to any new deployment
  (the bench server) along with `.env`.

The preflight guard checks the credential is present before a Docker claude run,
with an actionable message if it's missing. `codex` in Docker is not wired yet
(its packaging/auth needs confirming) — run codex via host mode for now.

## Why codex runs on the host

`opencode` and `claude` are installed in the runner image (`Dockerfile.runner`);
`codex` is not wired into it yet. Rather than baking its auth into a container,
the lab runs codex where it already works — on the host, via the **subprocess**
execution backend. The web run's Celery worker must therefore run on the host for
codex (the default `make app-up` worker is containerized and can't see it).
`claude` works in both: Docker with a token in `.env` (above), or host mode with
your own login.

A preflight guard (`RunService._preflight`) rejects a `codex` run unless the
backend is `subprocess`, and a Docker `claude` run without a credential in `.env`,
each with an actionable message — so you never get a silent "command not found"
inside a container.

## Model selection

The New-run executor menu is **harness-aware**: for `opencode` it's the live
gateway list; for `codex`/`claude` it swaps to that CLI's **native models**
(served by `GET /api/models/native`, from `native_models.py`) — the Claude 5.5
family and Fable 5.1 first, the previous generation after. The selected id is a bare
(non-`gateway/`) name, which
`bench_run.py` passes straight to the CLI's `--model` / `-m` (`_native_model`
ignores any `gateway/`-prefixed value and lets the CLI default apply).

Keep `HARNESS_NATIVE_MODELS` current as new CLI versions ship — that list is the
systematic-lab knob for host harnesses. The `codex` list starts with only the
confirmed default (`codex-mini-latest`); add your codex model ids there.

## Running codex / claude on tetris

One command from the repo root brings up redis + worker + API + frontend on the
host (Ctrl-C stops all):

```bash
make app-host
```

Or run the pieces in separate terminals for debugging: `make app-worker-host`,
`make app-api-host`, then `cd frontend && VITE_API_TARGET=http://localhost:8009
npm run dev`.

Then open `http://localhost:5173/advanced/runs`, pick **Case** `clinic-slots-t1`,
set **Harness** to `codex` or `claude`, and Create run. The run streams into the
detail page like any other and scores itself: the case's validation commands are its
graded tests.

`opencode`, SWE-bench and Docker `claude` runs use the containerized `make app-up`
(they need the runner image). The two modes don't run at once — choose per what
you're testing.

## Verified vs. to-verify

- **Unit-verified:** the exact CLI invocations (headless flags confirmed against
  each CLI's `--help`), the dispatch, `--harness` plumbing, and the preflight
  guard (`tests/harness_lab_app/test_harness_adapters.py`).
- **Verify on first real run:** that `codex --quiet --full-auto` and
  `claude -p --dangerously-skip-permissions` complete tetris headlessly and write
  `workspace/<run_id>/main.py` in your environment. Adjust the builders in
  `bench_run.py` if your CLI versions expect different flags.

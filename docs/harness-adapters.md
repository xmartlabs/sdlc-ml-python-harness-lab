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
artifact — only the CLI differs. Pick the harness in the New run form.

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

The preflight guard checks the credential is present before a Docker claude run,
with an actionable message if it's missing. `codex` in Docker is not wired yet
(its packaging/auth needs confirming) — run codex via host mode for now.

## Why codex / claude run on the host

`opencode` is installed in the runner image (`Dockerfile.runner`); `codex` and
`claude` are the CLIs you already have installed and logged in on your machine.
Rather than baking their auth into a container, the lab runs them where they
already work — on the host, via the **subprocess** execution backend. The web
run's Celery worker must therefore run on the host for these harnesses (the
default `make app-up` worker is containerized and can't see them).

A preflight guard (`RunService._preflight`) rejects a `codex`/`claude` run
unless the backend is `subprocess`, with an actionable message — so you never
get a silent "command not found" inside a container.

## Model selection

The New-run executor menu is **harness-aware**: for `opencode` it's the live
gateway list; for `codex`/`claude` it swaps to that CLI's **native models**
(`HARNESS_NATIVE_MODELS` in `frontend/src/pages/Runs.tsx`) — e.g. Claude Sonnet 5
/ Opus 4.8 / Haiku 4.5. The selected id is a bare (non-`gateway/`) name, which
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
`make app-api-host`, then `cd frontend && VITE_API_TARGET=http://localhost:8008
npm run dev`.

Then open `http://localhost:5173/runs`, pick **Case** `tetris-v1`, set
**Harness** to `codex` or `claude`, and Create run. The run streams into the
detail page like any other; score it manually (or against the smoke floor).

`opencode` and SWE-bench runs keep using the containerized `make app-up` (they
need the runner image and the gateway). The two modes don't run at once —
choose per what you're testing.

## Verified vs. to-verify

- **Unit-verified:** the exact CLI invocations (headless flags confirmed against
  each CLI's `--help`), the dispatch, `--harness` plumbing, and the preflight
  guard (`tests/harness_lab_app/test_harness_adapters.py`).
- **Verify on first real run:** that `codex --quiet --full-auto` and
  `claude -p --dangerously-skip-permissions` complete tetris headlessly and write
  `workspace/<run_id>/main.py` in your environment. Adjust the builders in
  `bench_run.py` if your CLI versions expect different flags.

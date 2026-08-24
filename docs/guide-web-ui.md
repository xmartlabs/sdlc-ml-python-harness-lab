# Web UI guide — Agentic Harness Lab

The local web app (Vite on `http://localhost:5173`, FastAPI + Celery behind it)
is the operator surface for the lab: launch benchmark runs, watch them execute in
isolated containers, score them, and compare harnesses and models. This guide
walks the end-to-end flow and what each screen does.

Start it with `make app-up` (brings up api + worker + redis + frontend).

## The core loop

```
pick a Case → choose model + harness + prompt → Create run
   → watch it execute (logs stream live)
   → it finishes: SWE-bench auto-scores; others wait for a manual score
   → Compare runs across models / conditions
```

Everything the web app runs also has a CLI equivalent (`make bench-run …`); the
form shows the equivalent command as a reference. The web lane and the CLI are
two front-ends over the same runner (`scripts/bench_run.py`).

## Screens

### Dashboard — the leaderboard
The landing page is the lab's **leaderboard**, swebench.com-style: every terminal
run counts as one attempt, grouped by **condition** (harness + executor model +
prompt variant + harness version) and ranked by **% resolved** with a
proportional bar.

- **% resolved is per-attempt**: resolved runs / objectively-validated runs of
  that condition. Manual-score cases (e.g. tetris) have no objective verdict, so
  they show avg/best score but no %.
- **Filters**: lane (case type), harness, model, prompt variant, and **harness
  version** — so you can ask "does harness v0.4.0 + conductor + qwen3.6-35b beat
  the bare model?" directly. The per-run version comes from `template_ref` in
  `metadata.json` (recorded at run time; older runs fall back to the harness
  fingerprint hash).
- **Official reference rows** (toggle) come from the curated file
  `data/reference/swebench_official.json` — edit it by hand to refresh (agent,
  model, `pct_resolved`, date, source URL per row). They render with an
  **oficial** badge as a thermometer against the real leaderboard.
- ⚠️ The comparison is **indicative**: your % runs over the lab's curated subset
  and your conditions; official rows are pass@1 over the 500 SWE-bench Verified
  instances.

### Runs
Where you launch and track runs. The **New run** form:

- **Case** — what the agent is asked to do (see Cases). Selecting a case sets the
  available **Prompt variants**.
- **Harness** — the agent scaffold under test: `opencode` (containerized,
  gateway models) or `codex` / `claude` (run on your host CLI with native auth).
  To test claude/codex, follow the
  [claude/codex runbook](runbook-claude-codex.md); design in
  [harness-adapters.md](harness-adapters.md).
- **Planner / Executor model** — chosen in three tiers, **source → provider → model**:

  1. **Source.** *AI Gateway* routes through LiteLLM (Langfuse traces, per-model
     `max_tokens` caps, one name mapping in the middle). *Direct (local)* talks
     straight to your own hosts — LM Studio at `LMSTUDIO_BASE_URL`, Ollama at
     `OLLAMA_BASE_URL`. The distinction used to be invisible: a heading reading
     "LM Studio (local)" actually held gateway aliases, so two very different
     routes looked like one menu.
  2. **Provider.** Chips with a count. Under the gateway: LM Studio, Ollama,
     OpenAI, Claude, Gemini, NVIDIA, Other — split by id prefix, the only signal
     LiteLLM's `/models` list carries; an id no rule matches lands in *Other*
     rather than disappearing. Under Direct: one chip per host that answered.
  3. **Model.** The list for that source and provider, launchable models first.
     Under the select, one line states what the pick means for a run: a direct
     model's loaded window, or *not routable* for a gateway model nothing can
     reach (enable it on the Settings page first).

  A gateway card can be routable and still be dead, and those are different
  problems: *not routable* means this harness has no way to address the id,
  *backend unavailable* means the machine behind the card is not serving it —
  the LAN host is off, or the card declares no `api_base` and LiteLLM would
  silently send the request to its own default host. The picker reuses the
  direct-host probe it already ran for the Direct source, so the check costs no
  extra round-trip, and it fails open: a host nothing probed keeps its verdict
  open rather than being hidden. Dead cards still sort below working ones and
  carry the reason in the line under the select and as a badge in Settings.

  Gateway models come **live from LiteLLM** — add one to the gateway, press
  **Refresh from gateway** in Settings, and it appears here and becomes routable,
  no hand-edited lists. Direct models come from the host itself, carrying the two
  facts that decide whether a run works: whether the model is loaded and with how
  much context. Their ids are the ones the host registers, so no alias can rename
  or reroute them in transit.

  A source with nothing in it stays **clickable and says why** — "LM Studio at
  http://…:1234 did not answer", per host, since one machine can be up while the
  other is down. A disabled button with no reason is the one thing this picker
  must not do.

  **⟳ Reload** re-reads everything, dropping the server-side caches. The lists
  have separate cache lifetimes, so this is the one action that guarantees a
  model you just loaded is visible.

  See [ADR-0012](adr/0012-dynamic-gateway-model-list.md),
  [ADR-0016](adr/0016-opencode-config-overlay-for-model-routing.md) and
  [the direct lane](local-model-runtime-config.md#the-direct-lanes-skip-the-gateway-when-you-are-iterating).
- **Score mode** — web runs are `manual` (interactive is CLI-only). SWE-bench
  cases auto-score objectively regardless.
- **Max steps**, **Resume**, **Dry run** — run controls.
- **Skills** ablation — disable governed skills or enable experimental staged
  skills *for this run only*, to A/B the harness. A non-empty ablation makes the
  run a distinct arm (`condition_hash`). See [ADR-0011](adr/0011-per-run-skill-ablation-and-staging.md).
- **Validate config** — runs the creation preflight (`POST /api/runs/validate`)
  without creating anything: case and harness availability, `GATEWAY_TOKEN`,
  whether the executor is routable and reachable, and the context window the
  backend is *actually serving* (ADR-0020). A green verdict means Create run will
  not be refused; on a cold model the first check pays the JIT load (~40s), and
  the measurement is memoized so pressing Create right after costs nothing extra.
  Editing any field clears the verdict, since it only describes the configuration
  that produced it.

#### Batch mode — queue a whole comparison at once

Every comparison worth making is more than one run: a harness A/B is two, a model
sweep is four, and telling a real difference from run-to-run variance means
repeating each of those. **Batch mode** builds that grid in one submission
([ADR-0023](adr/0023-batch-run-execution.md)).

Press **Batch mode** next to the preset button. Four of the single selects become
additive checkbox lists, seeded from whatever you currently have selected — so the
batch starts as "what I was about to run" and you widen it from there:

- **Cases**, **Prompt variants**, **Harnesses**, **Executor models** — each list is
  an axis. Every combination is queued.
- **Repetitions** — trials per combination.
- **Prompt variants** offers the *union* across the selected cases, not the
  intersection: cases legitimately define different variant sets, and an
  intersection would empty the axis as soon as one case is unusual. Each case is
  then paired only with the selected variants it actually defines, so selecting
  `tetris-v1` (which has `conductor`) alongside `cli-textstats-v1` (which does
  not) queues `conductor` for the first case only. **The stated run count already
  accounts for this**, which is why it can be lower than the product of the axes.
  A case that defines *none* of the selected variants is rejected rather than
  silently dropped — the batch would not be the one you asked for.
- Leaving **Executor models** empty means "do not vary the model" — the harness
  default is used for every run.

Planner model, max steps, dry run and the **Skills** ablation are held constant
across the batch. That is the point: those are the variables you are controlling.

The submit button states the cost before you pay it — **Queue 24 runs** — and
refuses to submit past `HARNESS_LAB_MAX_BATCH_RUNS` (default 50). The count is
worth reading: each unit is a Docker container spending model budget.

Validation is **all-or-nothing**. Every combination is preflighted before anything
is created, so a rejection means nothing was queued and there is no half-executed
batch to unpick. Preflight is deduplicated by executor and harness, so a 24-run
batch over two models pays two context probes, not twenty-four.

On success the form reports the `batch_id`. Paste it into the run list's search
box to narrow the table to exactly that batch; runs belonging to one carry a
**batch** badge.

`resume` is not available in batch mode — it resumes one specific workspace, which
means nothing when you are asking for N fresh trials.

The **run list** identifies each run by its **case title** (the same descriptive
name you see when importing a SWE-bench Pro instance), with the attempt id
(`K-009`), the short instance id, and the `F2P n / P2P m` test counts underneath.
Hovering the run cell shows a tooltip with the exact run id, the case id, the
test counts, and the full CLI command — so the row stays scannable and nothing
is lost.

It also shows, per run, its **harness** and **executor model** (parsed from the
command, for fast visual scanning), state, score, **created** age, and an
**Improvements** column.

Beside the state, a **checks failed** / **checks passed** badge reports the
automated verdict when one exists ([ADR-0021](adr/0021-benchmark-artifact-integrity.md)).
It matters most for manual-score cases: a run whose produced code does not even
start otherwise looks identical to one waiting for your review. No badge means
the checks have not run or the case has no objective criterion.

A failed run also says *why*, in one word, with the full reason on hover:

| Badge | Meaning | Counts as an attempt? |
|---|---|---|
| **lab error** | Never started — runner image missing, Docker unreachable, bad config | No |
| **worker restarted** | Killed mid-flight by a restart; retry resumes it | No |
| **endpoint down** | `run_max_stream_errors` provider requests failed back-to-back — or the run ended, however cleanly, with every model request it ever made having failed | No |
| **no response** | The provider accepted the request and answered nothing for `run_model_wait_seconds` | No |
| **stalled** | Ran, then went quiet for `run_stall_seconds` with no model request in flight | Yes |
| **timed out** | Exceeded `run_timeout_seconds` | Yes |
| **agent error** | The agent's own output ended in an error | Yes |

The first four carry a **no result** note and are **excluded from the leaderboard**:
nothing about the model or the harness can be read from them, and counting them
would inflate a condition's attempt total with runs the model was never given the
chance to get wrong. Retry them instead. The classification lives in
`src/harness_lab_app/run_failures.py`, so the list, the API and the leaderboard
share one definition.

- **Created** shows the run's age (`5h ago`); its tooltip carries the exact
  creation timestamp plus the last execution window — start → end and how long
  it took. Timestamps come from the API as naive UTC and are rendered in your
  local timezone (`frontend/src/dates.ts`).
- **Search / Harness / State** filter the list; **per page** (10/25/50/100) plus
  the pager at the bottom keep long histories navigable. Every column header
  except *Actions* is a **sort** toggle (click again to flip direction); the
  default is newest-created first.
- Filters and sorting are client-side over the runs already loaded — no extra
  API round-trip, and the counter in the panel header shows `N of M runs` while
  a filter is active.

The **Improvements** column tells you where the run stands in the improvement
cycle — derived from `data/runs/<id>/improvements.json`:

- **yes** — synthesis ran and proposed at least one change (go save/review them);
- **no** — synthesis ran and proposed nothing (clean run / outcome gate closed);
- **pending** — synthesis hasn't run yet (open the run and generate it).

Create the run and you land on its **detail page**: live logs, artifacts, the
validation & score panel, attribution, LLM audit, and in-platform sandboxes
(web terminal / desktop) to poke at the produced workspace.

#### When a run stops making progress

Four budgets end a run that will not end itself, and they catch different things:

- **Run timeout** (`run_timeout_seconds`, default 7200) — the total budget. It
  catches a run that is too *slow*.
- **Stall timeout** (`run_stall_seconds`, default 600) — time since the last line
  of output **while no model request is in flight**. It catches a run that is
  *stuck*, which the total budget structurally cannot: a hung run and a healthy one
  look identical from outside, so silence needs its own clock.
- **Model wait** (`run_model_wait_seconds`, default 2400) — the same silence, but
  while the provider owes the run a first token. It gets its own, much larger
  budget because a local model says nothing while it processes the prompt.
- **Endpoint errors** (`run_max_stream_errors`, default 5) — consecutive failed
  provider requests with no progress between them. opencode retries a failing
  stream forever, so without this a run spends its entire budget on an endpoint it
  cannot reach.

One failure shape slips past all four budgets: the run *ends on its own* before
any threshold trips. Run `…qutebrowser…-K-003` made two provider requests, both
rejected in two seconds by a gateway configuration error (`max_tokens` above the
model's completion cap) — fewer than `run_max_stream_errors` — and the harness
script then carried on to scoring, archived an empty diff and exited 0. Recorded
as completed, it would have gone on the leaderboard as the model attempting the
case and resolving nothing. The lab therefore checks one more thing at the end
of every run: if it made model requests and **not one of them was answered**, the
run is marked failed as **endpoint down** regardless of its exit code, and does
not count as an attempt.

A stalled run is marked **failed** with the reason and the last thing it said
before going quiet — normally the tool call it is wedged inside. Run
`tetris-v1-K-034` is why this exists: the agent shelled out to a pygame process
that caught SIGTERM and never died, the surviving grandchild held the stdout pipe
open, and opencode blocked reading it. Forty-seven minutes of nothing, state
"running", and only the two-hour ceiling would ever have ended it.

Which clock applies is read off opencode's own log, and getting that wrong is not
academic: with one 600s clock for both silences, every 35B run died at its first
turn while still processing the prompt and was filed as the agent wedging the
harness ([ADR-0022](adr/0022-watchdog-splits-model-wait-from-agent-stall.md)). A
model-side failure is never the agent's fault, so **no response** and **endpoint
down** do not count as attempts, while **stalled** does.

The tell, if you want to confirm by hand: `ls -l data/runs/<id>/opencode.log` (has
it moved?) and `docker top harness-run-<id> -eo pid,etime,stat,pcpu,args` (a child
whose elapsed time is absurd for what it is — a `timeout 2` alive for 47 minutes).
Traffic on your model host is the other signal, and it separates the two silences:
a stuck agent is blocked in a tool call, a prefilling model has your GPU pinned.

When a budget ends a run, the harness stops the container itself, so the exit code
that follows is its own SIGKILL (137) rather than anything the agent did. The run's
last event therefore leads with the reason and keeps the code as context — `Run
timed out after 7200s (exit code 137)` — instead of the bare `Run exited with 137`
that read like a crash the lab never had.

Run detail reports **Queued for** and **Execution** separately for the same reason.
The worker runs at concurrency 1, so a batch queues behind itself and a run can sit
for hours before its container starts; a single created-to-ended duration cannot
tell a slow model from a busy queue, which is exactly the question you are asking
when a run looks slow.

The clocks are visible read-only under Settings → Runtime facts. Any of them can be
disabled with `0` (`HARNESS_LAB_RUN_STALL_SECONDS`,
`HARNESS_LAB_RUN_MODEL_WAIT_SECONDS`, `HARNESS_LAB_RUN_MAX_STREAM_ERRORS`); see
[local-model-runtime-config.md](local-model-runtime-config.md) for how to pick
`run_model_wait_seconds` for a slow model.

### Cases
The catalog of tasks. Three ways to get realistic cases:

- **tetris-v1** — the seeded pilot (build a game; manual score + smoke floor).
- **SWE-bench** — import a real GitHub issue+fix from SWE-bench Lite/Verified via
  the *Import SWE-bench* box or `make swebench-import INSTANCE=<id>`. These
  **auto-score objectively** (resolved → 10). Only a validated subset runs in the
  generic runtime — see [swebench-curated-set.md](swebench-curated-set.md).
- **Objective agentic cases** — dataset-free `build_from_scratch` tasks with
  exit-code validation, e.g. `benchmarks/cli-textstats/`
  (`make case-import TASK=…`). Great for trying a new model on a self-contained
  task with an objective floor.

#### Managing a case from the browser

Selecting a case opens the **case editor** below the list. Everything a case is
made of is editable there, and nothing about the create → edit → run loop needs
a shell.

- **Case ID is fixed.** It is the key every run in the history joins on, so
  renaming it would orphan past results. Use **Clone** to start a variant.
- **Prompt variants** are the strategies you want to compare — one prompt each,
  named. The run form picks one by name, and the leaderboard groups by it.
- **Step prompts** apply to `multi_step` cases: step 1 uses the prompt variant,
  and each entry drives the step with the same number. Leave the list empty and
  the runner falls back to its generic continuation prompt.
- **Validation commands** run in the sandbox after the agent finishes; a
  non-zero exit fails the objective score. `{run_id}` and `{workspace}` are
  substituted.
- **SWE-bench cases are read-only.** Their prompts and test lists *are* the
  benchmark; editing them would silently invalidate every comparison already
  recorded. Clone one to build a variant you can change.

A case created here lives in the database only, which is all the runner needs:
the worker stages the case as `data/runs/<run-id>/case_spec.json` before the
container starts. That is what the `not_synced` badge means — not a problem, and
not something to fix before running.

**Sync to YAML** is the export path, not a prerequisite. It writes
`benchmarks/<case-id>/task.yaml` plus one prompt file per variant, so the case
can be committed to the repo and driven from the CLI lane. After a sync, later
edits show as `modified_since_sync` until you export again.

### Skills
Two tiers: the **governed catalog** (read-only, shipped with the harness) and
**staged skills** — experimental skills you trial per-run via the ablation panel
and promote when they earn their keep. The page includes a how-to for the A/B loop.

Staged skills are whole trees, not single files. Get one in three ways:

- **Import a skill** — a GitHub URL (repo, subdirectory or `blob` link), a raw
  `SKILL.md` URL, or an archive URL, optionally pinned to a ref;
- **Upload** a `.md`, `.zip` or `.tar.gz`;
- **New staged skill** — write the `SKILL.md` inline.

Import and upload return a **preview** first (file list, detected name and
description, warnings, total size) and write nothing until you confirm; replacing an
existing staged skill is a separate opt-in. **Edit** opens a file browser: pick a
file on the left, edit it on the right, add or delete files there too. Binary assets
are stored and materialized but not editable in the browser.

Everything lives in the database; `data/skills_staging/` is a rebuildable
materialization, so the git working tree stays clean while you experiment
([ADR-0017](adr/0017-multi-file-staged-skills-and-remote-import.md)). Editing any
file — including a helper script — changes the skill's `condition_hash`, so arms
stay distinguishable.

### Improvements
Cross-run worklist for the improvement cycle: after a run's proposals are
synthesized, the **Guardar** button on the run detail snapshots them here (one
row per proposal, with run date and template version for comparing iterations).

Each row shows the **harness** that produced the run (claude, opencode, …), and
the toolbar can **filter by harness** — useful to work claude-only or
opencode-only batches, since the harness decides which files a fix should touch.
Select any subset — across runs and cases — and **Generar issue combinado**
consolidates them, via one LLM pass plus the sanitizer pipeline, into a single
prefilled ml-python-base issue.

The generated issue opens with a deterministic **Target harness surface** block,
computed from the selection's harnesses (never by the LLM):

- all proposals from **one harness** → that harness's adapter (e.g. claude →
  `CLAUDE.md` + `.claude/`; opencode → `AGENTS.md` + `opencode.json`);
- **mixed harnesses** → the shared governed `.github/` layer (synced to every
  adapter);
- if any run used a **self-hosted model** (`gateway/…` executor), a note reminds
  that weaker models benefit from more explicit, front-loaded governance.

The same directive steers the LLM's `affected_artifacts`, so the issue lands in
the surface that actually influenced the runs. Details in
[improvement-cycle.md](improvement-cycle.md).

### Compare
Line up runs of the same case across models or harness conditions:

- Pick 2–4 runs; the **metrics table** shows case, models, prompt, objective
  result, final/composite score, tokens, cost, time, steps, and interventions —
  with the best cell per row highlighted.
- The aggregated ranking by condition (% resolved, harness version, official
  reference) lives on the **Dashboard** leaderboard.

Typical use: run the same case with a new model and a previous one, then open
Compare to see the objective and cost deltas.

### Health
What the lab can reach, and where its generated state has drifted
([ADR-0018](adr/0018-health-and-drift-panel.md)). One row per check, worst first,
refreshing every 30 seconds:

- **AI gateway / Redis / Celery worker** — is the stack actually wired up. No worker
  reply means runs will sit in `pending`.
- **Docker runtime** — reported *by the worker*, because the API has no docker socket
  by design ([ADR-0002](adr/0002-celery-docker-per-run-execution.md)). Covers the socket
  itself and whether the runner image has been built.
- **Generated skill views** and **governed git tree** — the drift signals. A run that
  died mid-ablation leaves the tracked tree modified; this is where you see it.
- **Staged-skill materialization** and **model routing overlay** — the `data/`
  derivations. They tell you when a model you enabled is not routable yet, or when
  `data/skills_staging/` disagrees with the database.
- **Run queue** — runs active for over six hours, which usually means a worker died
  under them.

A failing row carries its own **repair button**. There are exactly five repairs —
re-sync skill views, rebuild the staging tree, refresh the model catalog, reconcile
stuck runs, build the runner image — and nothing else can be triggered from the
browser. Repairs are never automatic: `skills_sync` rewrites tracked files and
reconciling fails live runs, so both wait for you to ask.

**Build runner image** covers the one condition that used to send you to a
terminal on a fresh machine. The build runs on the worker (the only process with
the socket) against its own `/app` mount of the repo, takes a few minutes, and
does not hold the request open — re-check the panel when it finishes. Until the
image exists, creating a run is refused at preflight rather than queued and
killed milliseconds later.

### Settings
The backoffice: everything you would otherwise change with a shell on the server.

- **Gateway connection** — base URL and token, with **Test connection** probing the
  pair without saving it. Saved values take effect on the next request; no restart.
  Secrets are shown masked and are never sent back to the browser in the clear —
  leave a secret field blank to keep the stored value.
- **LM Studio (direct, no gateway)** — what the host at `LMSTUDIO_BASE_URL` is
  serving right now, read from its own API: model id, state, loaded context and
  maximum window. Nothing is adopted or stored; **Refresh from LM Studio** re-reads
  the server, so loading or ejecting a model there shows up here on the next click.
  Set the URL under *Local runtimes*, including the `/v1` segment — a run using one
  of these models is refused up front if the host is unreachable, the name is not
  served, the model is not loaded, or its window is under 32k. That strictness is
  deliberate: unlike the gateway probes there is no ambiguity to protect.
- **Model catalog** — **Refresh from gateway** adopts whatever LiteLLM serves right
  now. Each model can be renamed, enabled/disabled, or made the default. *Enabled*
  models are routed to opencode through a config overlay at run launch, so a model
  you enable here works immediately while `opencode.json` stays untouched
  ([ADR-0016](adr/0016-opencode-config-overlay-for-model-routing.md)). A model the
  gateway stopped serving is marked *not served* rather than deleted, so its label
  survives a provider being down. Ids the gateway routes to the same backend model
  are marked *merged into …* and appear once in the run picker;
  **Check context** measures the window a backend is really serving and flags
  anything under 32k, which run creation also refuses
  ([ADR-0020](adr/0020-context-window-preflight-and-alias-merge.md)).

  The catalog table and both run-form pickers are grouped by serving backend in a
  fixed order — **LM Studio (local) → Cloud → Ollama (rarely used)** — because the
  gateway serves ~40 aliases and a flat alphabetical list interleaved the local
  backend this lab runs on with `ollama-*` routes that are usually down. Within a
  group, models you can launch come before ones nothing can route, and models you
  have named come before ones still carrying their bare gateway id, so the default
  executor sits at the top of the menu rather than buried in it. Backends are
  matched by id prefix; anything unprefixed falls into Cloud, so a new backend
  shows up in the menu instead of disappearing from it.
- **Model defaults / Local runtimes / Harness credentials / Tracing** — the
  allowlisted slice of `.env` ([ADR-0014](adr/0014-web-editable-env-allowlist.md)).
  Every write is atomic and keeps a rolling backup in `data/app/env-backups/`.
- **Runtime facts** — `HARNESS_LAB_*` settings, read-only: they are cached at
  process start and changing them needs a restart. This includes
  `max_batch_runs` (default 50), the ceiling the run form reads to size a batch —
  the form reads it rather than mirroring it, so the two cannot disagree.

The password is deliberately **not** on this page: it is set on the server with
`make set-password`, so a stolen session cannot lock you out of your own lab
([ADR-0015](adr/0015-single-user-session-auth.md)). Until you set one, the lab runs
in *open mode* — every screen carries a banner, and anyone who can reach the address
can drive it. Once set, the app opens on a password prompt and the sidebar gains a
**Sign out** button; sessions last 7 days and `make set-password ROTATE=1`
invalidates all of them at once.

## Continuous-improvement cycle

The lab feeds improvements back to your harness repos: after a run's LLM audit,
`make improve RUN=<id>` synthesizes GitHub issue proposals from the "needs
improvement" signals (skills, governance docs, subagents), and `CREATE=1` files
them in `ml-python-base` / `harness-engineering-guide` on your approval. See
[improvement-cycle.md](improvement-cycle.md).

## Testing a new model — worked example

Fastest path, no gateway: load the model in LM Studio with an explicit context
length, press **Refresh from LM Studio** on the Settings page, and pick it from the
**LM Studio (direct, no gateway)** group in the run form. Use the gateway path below
when you want the run's traces and costs in Langfuse.

1. Register the model in your LiteLLM gateway (your ai-gateway).
2. Settings → **Refresh from gateway**. The model is adopted, enabled and routable
   immediately; `opencode.json` is not touched. (Once it earns its keep, run
   `make sync-gateway-models` to curate it into the tracked map for the CLI lane —
   that is a promotion, not a prerequisite.)
3. Runs → New run → pick a case (e.g. `pytest-dev__pytest-10051` or
   `cli-textstats-v1`), set **Executor model** to the new model, Create run.
4. Repeat with the previous model (same case + prompt).
5. Compare → select both runs → read the objective/score/cost deltas.

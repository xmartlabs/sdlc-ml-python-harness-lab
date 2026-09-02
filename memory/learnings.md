# Learnings

> Non-obvious facts discovered while working: gotchas, why-it-is-this-way, dead ends
> to avoid. Append new entries at the top. One fact per entry.

## Resetting the database under a live stack splits it in two — 2026-09-02

`make app-reset-db` runs `scripts/reset_db.py` from the **host** while the `api`
and `worker` containers hold `data/app/harness_lab.db` open across a bind mount.
SQLite coordinates concurrent connections through a WAL plus a `-shm`
shared-memory index, and that index is not coherent across the container/host
boundary on macOS (virtiofs): the host process believes it is the only
connection and unlinks the `-wal`/`-shm` the containers are still using.

The two containers then read **different files**, and nothing errors. The API
keeps serving and writing to the file it already had open; the worker reads the
new one. An experiment created afterwards shows its runs as `pending`, Celery
delivers `execute_run`, the worker looks the run up, gets `None`, returns, and
logs `succeeded in 0.004s`. The runs never execute. The API's rows exist only in
an unlinked inode and are lost the moment the container restarts — so the
symptom is discovered hours later and the evidence is destroyed by the fix.

Diagnosis shortcut: `docker exec <container> sh -c 'ls -l /proc/*/fd' | grep
'\.db'`. A `(deleted)` marker on `-wal`/`-shm` means the split has already
happened. Comparing a row count from inside the worker against the same query
from the host confirms it in one step.

`app-reset-db` now refuses while `api` or `worker` are running. The same hazard
applies to any host process that opens the database while the stack is up,
including an ad-hoc `sqlite3` session — closing it unlinks the sidecar files.
Read the database through `docker compose exec api`, or stop the stack.

Not fixed at the root: the database still lives on the bind mount. The
structural fix is a named volume (WAL and `-shm` on the VM's own filesystem) or
having the worker reach the API over HTTP instead of sharing a file.

## A comparison fails silently in five places at once — 2026-09-02

The first real two-arm corpus (12 Tetris runs) produced a verdict that looked
complete and was unreadable. Five independent mechanisms, none of which raised
anything:

1. **`clone_run` dropped `experiment_id`/`condition_label`.** They live on `Run`,
   not `RunConfig`, so the careful field-by-field config copy did not carry them.
   Four "Run again" repetitions executed the design and counted towards nothing.
   This is the *third* time this exact shape has bitten (see the continuation
   entry below): fields on the row rather than the config are the ones that get
   dropped.
2. **Only SWE-bench auto-scored.** Everything else parked in
   `awaiting_manual_score` forever, so the comparison fell back to
   `task_validation_pass` — for tetris-v1 a smoke check ("does it start"), which
   is level_2 of a 0-10 rubric and cannot fall once both arms start. It reported
   100% → 100% as *no difference observed*.
3. **The stop criterion was arm-dependent.** `_STOP_RE` matches a prose sentinel
   the `sdlc_full` prompt teaches and `naive` does not, and the no-progress
   fallback existed only in the SWE lane. `agent_steps 10 → 1` measured prompt
   authorship. Verified against the artifacts: K-009 had ten `# claude run
   started` headers, K-011 had one and ended at `turns=50` — roughly 70 vs 50
   turns of real work, reported as a 10× efficiency win.
4. **Zero observed spread cleared the noise floor.** `stdev=0.0` on both arms
   made `abs(delta) > 0.0` true, so three "beyond noise: yes" verdicts rested on
   having measured the same thing twice.
5. **`runs.yaml` out-voted `--harness`.** A web run whose id collided with a
   pre-declared cell ran one CLI and recorded another (K-001..003: config
   `claude`, `metadata.json` `opencode`), while the *model* flags used the
   opposite precedence. Inconsistent precedence between two flags on the same
   command is a bug generator.

**Why it matters:** not one of these produced an error, an empty field, or a
suspicious-looking number. Each one produced a plausible result for a comparison
that was not being made. An evaluation lab's failure mode is not the crash — it
is the finding.

**How to apply:** before reading any experiment verdict, check (a) can the
success metric physically move for this case, (b) did both arms terminate under
the same rule, (c) is there any observed spread at all. Those three questions
would have caught four of the five. And when a stored file and a CLI flag can
both name the same thing, decide the precedence once and announce a divergence
rather than resolving it silently. See
[ADR-0038](../docs/adr/0038-every-run-leaves-with-a-score.md).

## An evaluation lab's real product is the refusals — 2026-08-30

Building v2 (experiments, scores, findings) the pattern that kept recurring is that
the valuable code is what the system *declines* to say:

- an experiment that varies harness **and** repository is rejected at creation, not
  rendered as an average later (`validate_design`);
- a mean from one observation reports **no** spread — `stdev=0.0` would claim perfect
  consistency from a single run;
- a delta with no spread to compare against reports `exceeds_spread=None`, not `True`;
- a missing cost emits **nothing**, never `0` — zero is a claim, and a flattering one;
- a confidence interval under N=3 returns `None`;
- a maturity level above 2 without evidence is refused outright;
- the DeepEval adapter emits nothing when unwired, because "not measured" and "scored
  badly" must never merge in an aggregate.

Each of these is one branch. Together they are the difference between a lab and a job
runner that renders averages — and none of them would ever be missed by a test that
only checked the happy path, because the failure is a plausible-looking number.

Second recurring rule, now applied five times: **a new field enters `condition_hash`
only when it differs from the historical default** (`skills_disabled` non-empty,
`target` present, `harness_source != lab_root`). Break it once and the whole
historical corpus re-bins against hashes it never had.

Third: an axis added to `RunConfig` must be carried by every path that claims to
reproduce a run — clone, retry, continuation, and each repetition of a batch. That is
four call sites per axis, and missing one splits an arm invisibly.

## cwd *is* the harness condition, and the fingerprint has to follow it — 2026-08-30

Claude Code and opencode auto-load their adapter file and native skills from the
process cwd. `bench_run` already knew this — it set `agent_cwd = workspace` for one
hardcoded case so a "bare" SWE arm would not silently inherit the lab harness. So the
`native` / `injected` comparison (ADR-0031) is not new machinery, it is that side
effect promoted to a declared condition.

The trap is in the fingerprint, and it fails **silently**. `build_harness_fingerprint`
reads `skills-lock.json`, which a cloned repository never has. Point both arms at it
and they produce the same `harness_hash` → the same `condition_hash` → one arm where
there should be two, and every downstream comparison quietly stops comparing. No test
fails, no error appears; the numbers just become meaningless. Hence
`build_workspace_harness_fingerprint`, which hashes what is actually readable from the
agent's cwd (native skill dirs, projected agents, adapter files, governance docs).

Three smaller things the implementation forced:

- **Project before the baseline commit.** Provisioning commits the workspace so
  `git diff HEAD` is only the agent's work. A harness copied in afterwards lands in
  that diff as though the agent wrote it.
- **Copy, never symlink.** `link_tool`'s symlink strategy encodes a path relative to
  the root it writes into; a lab skill is unreachable that way from a clone, and the
  archived workspace has to stand alone anyway.
- **Gate the fingerprint move on the declared condition, not on `agent_cwd`.** The SWE
  lane has set `agent_cwd = workspace` since long before this existed; keying off it
  would re-bin every historical SWE arm against a hash it never had.

**And do not compare `lab_root` against `native`** — they differ in the harness *and*
in whether the agent works inside the repo at all. Two changes, one number. See
[ADR-0031](../docs/adr/0031-harness-source-is-a-run-condition.md).

## A pinned commit must survive every path that "repeats" a run — 2026-08-30

Target repositories (ADR-0029) resolve a ref to a SHA at run creation. The obvious
implementation resolves it once, in `create_run`, and is wrong in four places:
`clone_run`, `continue_run`, `_resume_config` (retry) and `create_batch` all produce
runs that claim to be the *same arm* as an earlier one. Any of them re-reading
`main` silently splits one arm in two the moment someone pushes — and the split is
invisible, because both runs are labelled `main`.

So resolution takes a `pinned_commit` short-circuit, and every one of those paths
passes the stored commit instead of the ref. `create_batch` resolves once per
`(repo, ref)` and hands the same SHA to all N repetitions, for the same reason
`_preflight_key` deduplicates preflight: the expensive call is not the problem, the
*inconsistent* call is.

Two smaller traps in the same change:

- **`condition_hash` must hash the commit, never the ref.** Hashing `main` makes two
  different experiments share an arm. And it enters the identity *only when
  non-empty*, following the existing ablation rule — otherwise every historical arm
  re-bins.
- **`git ls-remote <url>` with a url starting with `-`** is argument injection
  (`--upload-pack=…` is remote command execution). `validate_repository_url` is a
  security boundary, not input tidying; and `GIT_TERMINAL_PROMPT=0` +
  `ssh -oBatchMode=yes` turn a missing credential into an error instead of a hung
  API worker.

**Also discovered, and it changes what is feasible next:** `skills_sync` is already
fully root-parameterized (`discover_skills(root)`, `link_tool(root, …)`,
`render_tool(root, …)`, `project_agents(root, …)`). Only
[bench_run.py](../scripts/bench_run.py) hardcodes `REPO_ROOT`. Injecting the harness
into a *foreign* workspace is therefore a `source_root`/`target_root` split, not a
rewrite — which is what makes the `native` vs `injected` comparison in
[the v2 roadmap](../docs/roadmap-lab-v2.md#phase-2--harness-conditions-on-an-external-target)
tractable.

## Langfuse v4 takes traces only over OTLP, and its SDK cannot backdate them — 2026-08-27

Wiring run traces to Langfuse looked like a two-line SDK call. It was not:

- **`POST /api/public/ingestion` returns 400** for every event type except
  `score-create` and `sdk-log` on v4, and `/api/public/traces|spans|generations`
  return 404. The only route that accepts spans is **OTLP over HTTP** at
  `POST {host}/api/public/otel/v1/traces` — protobuf *or* JSON, no gRPC — with
  `Authorization: Basic b64(pk:sk)` and the required header
  `x-langfuse-ingestion-version: 4`.
- **`langfuse` SDK 4.15.0 `start_observation()` has no `start_time`/`end_time`.**
  It stamps spans from the live OTEL clock, so it physically cannot express when
  a step that ran last month actually ran. Any post-hoc exporter must write
  `startTimeUnixNano`/`endTimeUnixNano` itself.

So `observability/` hand-builds OTLP/JSON and posts it with `urllib`. Attribute
names matter and are not guessable: `langfuse.trace.name`, `langfuse.session.id`,
`langfuse.trace.tags`, `langfuse.observation.type|input|output|level`,
`langfuse.observation.metadata.<key>` (that prefix is what makes a key
filterable in the UI), `langfuse.observation.usage_details` / `.cost_details` as
JSON *strings*. In OTLP/JSON, int64 is encoded as a string (`{"intValue": "7"}`)
and `bool` must be checked before `int` — `bool` is a subclass of `int`, so the
naive order encodes `True` as `1`.

**Why it matters:** the no-dependency choice is not purity. `Dockerfile.runner`
stays unchanged, which is the precondition for a future live emitter importing
the same module from inside a run container.

**How to apply:** when an observability vendor's SDK looks like the obvious
answer, check first whether it can express *historical* time. If it cannot, the
wire format is the API. See
[ADR-0027](../docs/adr/0027-langfuse-traces-rebuilt-from-run-artifacts.md) and
[guide-langfuse-observability.md](../docs/guide-langfuse-observability.md).

## SWE-bench score IS the objective validation — and the loop must stop on it — 2026-07-09

First real agent SWE-bench run (pallets__flask-4992-K-001, qwen3.6-35b) resolved
the issue in STEP 1 (`resolved=True F2P=1/1 P2P=18/18`), then burned 9 more
steps creating a main.py Flask demo and "fixing" werkzeug deprecations, because
the continuation prompts (`generate_continuation_prompt`) and `_STOP_RE` were
Tetris-hardcoded. Fixes: (1) SWE lane now has its own continuation prompt
(issue + live F2P/P2P status, "library source only, no main.py"), (2) the step
loop runs `swebench_common.evaluate` after EVERY step and breaks on resolved —
the stop criterion is objective, never the agent's own claims. (3) The worker
auto-scores swe_bench_instance runs from the validation verdict (resolved→10,
else→0, binary like official SWE-bench) and completes them — no manual score.
Re-validate also triggers auto-score (heals runs parked awaiting_manual_score).

**UI gotcha**: objective validation runs AFTER the state flips to
awaiting_manual_score and takes ~1 min (repo reinstall), so a single fetch on
state-change shows "No validation yet" forever — the RunDetail now retries the
validation/attribution fetch for ~2 min after a terminal state.
**Runner image gotcha**: jinja2 is a dev-group dep, but bench_run's skill
ablation imports the skills_sync renderer inside the container → added to
Dockerfile.runner explicitly (like pygame). Rebuild the runner image
(`docker compose build` / the runner build target) to pick it up.

## Per-run skill ablation = regenerate the generated views, not move files — 2026-07-08

Skill ablation (ADR-0011) works because the agent-visible skill surfaces
(`.opencode/skills/` symlinks AND the `BEGIN/END GENERATED SKILLS` block in
OPENCODE.md) are all *generated* by skills_sync from `.github/skills*`. So
"disable a skill for one run" = `link_tool(root, tool, filtered_skills)` +
`render_tool(..., skills=filtered)` per tool, and "restore" = the same call
with the full list — idempotent, which is what makes restore-at-start a valid
crash-recovery strategy (plus `atexit` for normal exits; worker concurrency 1
rules out interleaving). Removing symlinks by hand would have left OPENCODE.md
still advertising the skill (not a real ablation) and required bespoke cleanup.

Two hash traps: (1) `condition_hash` must include `skills_disabled`/staged
hashes ONLY when non-empty, or every historical arm re-bins; (2) the harness
fingerprint hashes skills via `skills-lock.json` (source, unchanged by
ablation), so without the explicit field an ablated arm would hash identical
to baseline except via the OPENCODE.md governance hash — too subtle to rely
on. Also exclude OPENCODE.md from `harness_git_dirty` during ablation.

**How to apply:** any per-run harness variation (agents, hooks) should follow
the same pattern: mutate only generated artifacts through the engine, record
the variation explicitly in metadata + condition hash, restore by full re-sync.

## LLM structured output: separate JSON-only call + tolerant parser — 2026-07-08

The audit's per-item verdicts (útil/redundante/mejorar per skill/doc/hook) come
from a SECOND gateway call with a "respond with ONLY a JSON array" prompt, not
from parsing a fenced block out of the prose audit. `parse_findings` never
raises: strips fences, extracts the outermost `[...]` from prose, drops invalid
items, coerces unknown enums, and returns `{"items": [], "parse_error": ...}`
on total failure — the audit.md is already written by then, so findings trouble
can never fail the audit. The UI treats findings as optional garnish.

## 2026-08-18 — A gateway card can be routable and dead, and `os.environ/` hides it

Every `gateway/lmstudio-qwen3.6-35b*` card failed while the same model answered fine
on the Direct lane. Nothing was mis-named: `/model/info` showed
`lmstudio-qwen3.6-35b` -> `lm_studio/qwen/qwen3.6-35b-a3b` @ `http://192.168.0.223:1234/v1`,
exactly LM Studio's id, and 19 completed runs on that alias prove the wiring. The rig
was simply off — ping 100% loss, ARP incomplete — and the Direct lane looked alive
only because its 15s TTL cache still held the last good listing.

Two structural findings behind it:

- **`os.environ/VAR` does not disable a card when the variable is unset.** LiteLLM
  resolves it to `None` and then sends the request to its *own* default host. The
  deployed gateway serves `lmstudio-qwen3.5-9b-mac16` with `api_base: null` — a card
  that can never work but advertises itself as available.
- **Fallback targets are not validated at startup.** `gemini-2.0-flash` sat in the
  chain after Google retired it (404 verified), so the escalation a powered-off GPU
  depends on was dead while the config read as configured.

**Why it matters:** the lab's `routable` flag answered "can this harness address the
id?", never "is the machine behind it serving the model?", so a dead card looked
launchable and the failure got blamed on the model or the alias.
**How to apply:** `build_model_catalog` now takes the `DirectHostProbe` list the
Direct lane already collected (no extra round-trip) and marks self-hosted gateway
cards `reachable: false` with a reason — host down, id absent from that host, or no
`api_base` declared. It fails open: an unprobed host keeps its verdict `None`. When a
gateway card fails, check the backend host before touching the alias. In ai-gateway,
`test_every_fallback_alias_is_a_declared_card` is now the startup check LiteLLM does
not do.

## Continuing a run: a child run was the cheap design, and two bugs sat next to it

**What happened:** adding "continue a finished run with a new prompt" looked like
it needed runner support — step offsets, merged usage, re-opened artifacts. It
needed none. Modelling the continuation as a *new run* whose workspace is copied
from the parent means it owns its own `data/runs/<id>/`, so there is nothing to
offset or merge. Two flags that already existed did the rest: `--resume` makes
`validate_workspace` keep the seeded tree, and `provision_workspace`
short-circuits on an existing `.git`, so a rehydrated SWE workspace is not
re-cloned. The only genuinely new code was one `shutil.copytree` and a prompt
header telling the agent the inherited workspace is its own.

The one place the lane mattered: a curated case (`RUNS_FILES`) resolves its
prompt from `benchmarks/<case>/`, ignoring `case_spec.json` entirely — so a
continuation had to override `_prompt_source.md` *after* the lane branch, not
inside the DB branch.

Two adjacent defects surfaced while wiring it, both in the same shape — a
`RunConfigCreate` rebuilt field by field, silently dropping the fields added
later:

- `tasks._config_payload` omitted `skills_disabled`/`skills_extra`, and
  `execute_run` overwrites `run.command` with what it rebuilds. **The skills
  ablation never reached the container.** The stored preview command had the
  `--disable-skill` flags; the executed one did not. Every ablation arm run
  before 2026-08-24 measured the full harness.
- `_resume_config` (retry) and `clone_run` dropped the same two fields, so a
  retry or clone quietly changed arms.

**Why it matters:** ADR-0011's whole point is that an ablation is a distinct
condition. A silent drop does not fail — it produces a plausible number for a
condition that was never run, which is worse than an error.

**How to apply:** when a feature can be expressed as a new row rather than a
mutation of an existing one, price that first — immutable artifacts make most of
the hard parts disappear. And treat every hand-rolled `RunConfigCreate(...)` as a
place a future field will be dropped: `_config_payload`, `_resume_config`,
`clone_run` and `continue_run` must all be updated together when the schema
grows. See [ADR-0026](../docs/adr/0026-run-continuation-as-child-runs.md) and
[guide-run-continuation.md](../docs/guide-run-continuation.md).

<!-- Add new learnings above this line -->

## "Audit a third-party repo" fails at provisioning when setup_commands mismatch the language — 2026-08-31

Every audit run against the `deepseek` target (`deepseek-ai/deepseek-harness`) died
with a generic "agent error"/`failed` (exit code 1) in `[1/5] Preparing`, before the
agent ran a single step. Root cause was not the harness, the model, or the network:
the target's `setup_commands` were `["pnpm install --frozen-lockfile", "pip install -e ."]`
and `validation_commands` `["pnpm test", "pytest -x -q"]` — a **JS/pnpm repo with
Python setup lines bolted on**. `_provision_git_target` in `scripts/bench_run.py`
runs each `setup_commands` entry as operator-authored shell (`subprocess.run(...,
shell=True)`) and `sys.exit`s on the first non-zero rc. `pip install -e .` on a repo
with no `setup.py`/`pyproject.toml` returns rc=1 ("does not appear to be a Python
project"), so the run stops with "The agent would have started against a half-built
repository". The UI surfaces only the terminal `failed` chip, so it reads as an agent
fault.

Two structural notes: (1) `setup_commands`/`validation_commands` are free-text shell
with **no language validation** at target creation — the DB will happily store a
command whose toolchain the repo cannot satisfy. The `Dockerfile.runner` image
**does** ship Node 20 (npm/pnpm/yarn) *and* Python 3.11 (pip/uv/pytest) plus git —
so `pnpm install` actually **succeeded** here; the abort was `pip install -e .` on a
repo with no `pyproject.toml`/`setup.py`. The runner does **not** ship Go or Rust.
So the real invariant is not "must be Python" but "each configured command's
toolchain must exist in the repo AND in the runner image". (2) the failure is
pre-agent, so `STEPS PARSED 0` and "No metadata.json" are expected, not extra bugs.

**Fix applied:** added a clean Python target `click` (`pallets/click`, `main` →
`36baa15`) via `POST /api/targets` — zero runtime deps, `requires-python >=3.10`,
test deps just `pytest`, so `setup_commands=["pip install -e .", "pip install pytest"]`
and `validation_commands=["pytest -q"]` install in seconds on the runner's Py3.11.
Archived the `deepseek` target so it cannot be reused.

**How to apply:** the runner image supports **Python and Node**, not Go/Rust/Java.
A target is safe when it is a pip-installable Python project (`pyproject.toml`/
`setup.py`) OR a Node project (`package.json`), and every configured setup/validation
command names a toolchain the repo actually has (don't put `pip install -e .` on a
repo with no `pyproject.toml`, or `pnpm install` on a repo with no `package.json`).
Before creation, inspect a repo with `POST /api/targets/inspect` (or per-target
`GET /api/targets/{id}/readiness`): the "Runtime compatibility" check warns — fail-open,
not blocking — when the ecosystem is unsupported or a command's toolchain is missing.
`readiness_service.runtime_compatibility(paths, setup, validation)` is the rule.

## In-platform sandboxes: worker-launched containers with dynamic host ports — 2026-07-08

Per-run validation sandboxes (ADR-0010) let the user run/inspect a run's code in
the browser: a `terminal` (ttyd) and a `desktop` (Xvfb+x11vnc+noVNC, to SEE a
pygame/Tetris window). Key mechanics:
- **One image, two modes** (`Dockerfile.sandbox`, ~870 MB, build-only) selected by
  `MODE` env in `scripts/sandbox_entrypoint.sh`. ttyd needs an arch-aware static
  binary (`ttyd.aarch64` on Apple Silicon). Xvfb screen size = what pygame's
  `display.init()` reports (proved the display works even without xdpyinfo).
- **Worker owns the socket** (ADR-0002): API can't launch; `POST /sandbox` marks a
  `Sandbox` row `starting` + enqueues `start_sandbox`; frontend polls `GET`; iframes
  `url`. Launch uses `containers.run(ports={'7681/tcp': None})` then
  `reload()` + read `container.ports[...]['HostPort']` — **docker-out-of-docker maps
  the port on the HOST**, so the browser reaches `localhost:<dynamic-port>`.
- **Isolation**: mount a COPY at `data/runs/<id>/sandbox-<kind>/` (host path), never
  the archived workspace, so exploring doesn't mutate the artifact.
- **Cleanup**: explicit Stop + `cleanup_run_container` (extended to also kill sandbox
  containers) + `delete_run` deletes `Sandbox`/`Audit` rows. Startup reaper + idle
  TTL are still-open hardening.

**Gotcha**: in zsh `status` is a read-only special var — don't use it as a loop
variable in test scripts (use `st`). Security: ttyd/noVNC have no auth — fine only
because it's local single-user on localhost.

## SWE-bench generic runtime needs per-repo env pinning (Python + dep constraints) — 2026-07-08

Running a SWE-bench instance with a generic `git clone + uv pip install -e . +
pytest` (instead of the official per-instance Docker images) works for
pure-Python repos, but **only with the right pinned environment**: (1) the
correct Python version (SWE-bench flask 2.3 needs **3.11** — under 3.13 old
werkzeug hits `ast.Str` removal errors), and (2) dependency constraints the
repo's own `pyproject` doesn't enforce (flask 2.3 declares `Werkzeug>=2.2.2`
but its code imports `werkzeug.urls.url_quote`, removed in werkzeug 3.0 → must
pin `Werkzeug<3`). The HF dataset rows carry NO dependency pins, so the lab
keeps a small `_ENV_SPECS` map (`scripts/swebench_common.py`) of
`repo -> {python, pins}`; adding a repo = add its entry. Also install the repo's
compiled `requirements/tests.txt` when present (pins test deps like blinker).

Parsing: `pytest -rA` prints per-test summary lines as `PASSED <nodeid>`
(status FIRST) — the regex must match status-first, not id-first.

**Why it matters:** without the pin/version the repo won't even import, and the
F2P/P2P validation silently reports 0/N. The golden-patch self-test
(`make swebench-selftest INSTANCE=…`) proves the whole harness with **zero model
tokens** (golden → resolved, empty → not) and is the right first de-risk before
wiring an agent. Flagship instance: `pallets__flask-4992` (add `text=` to
`flask.Config.from_file`), F2P=1 P2P=18, installs in seconds.

## LLM audit runs in the worker by shelling out to bench_audit.py — 2026-07-07

The qualitative audit (`scripts/bench_audit.py`, an LLM HTTP call up to minutes)
is exposed in the web via the same enqueue+poll pattern as validation (ADR-0008).
`audit_run` (Celery task) shells out to `scripts/bench_audit.py` with
`env={**os.environ, **build_run_environment()}` so the `.env` gateway URL's
`localhost` is rewritten to `host.docker.internal` (the worker is a container),
then reads `data/runs/<id>/audit.md` back and stores it on the new `Audit` row
(status running→done|failed). `AuditService` owns only DB/status (no gateway/
subprocess) so the API can import it. `bench_audit.py` got the same
`resolve_or_synthesize_run` treatment as bench_run so web `K-NNN` runs (absent
from runs.yaml) can be audited.

**Why it matters:** reuses the battle-tested CLI as the single source of truth,
avoids an `src → scripts` import coupling, and keeps the worker as the home for
slow/side-effecting work (ADR-0002).
**How to apply:** any new "read run artifacts + call an LLM/gateway" feature
belongs in the worker with `build_run_environment()`, surfaced via a DB status
row the frontend polls — not inline in the API (would block the request).
On Linux the worker needs `extra_hosts: host.docker.internal:host-gateway`
(Docker Desktop/macOS resolves it by default).

## Web runs are "managed": --fresh owns the workspace lifecycle end-to-end — 2026-07-07

The old "set RESUME=1" failure came from `validate_workspace()` (`bench_run.py`)
`sys.exit`ing on a stale `workspace/<id>/`, combined with the web/manual flow
`return 0`-ing before Phase 5 `close_run()` (which is interactive-only), so
workspaces were never cleaned. Fix: the runner adapter always passes `--fresh`
for web runs (ADR-0006). `--fresh` means "managed/automated run": on start it
resets a stale workspace instead of exiting; at the manual return (the web happy
path, after execution) it calls the new `finalize_workspace()` to archive
`workspace/<id>/` into `data/runs/<id>/workspace/` and remove it. CLI manual
runs omit `--fresh` so `make bench-close` can still find the live workspace.

**Why it matters:** with auto-incremental unique ids (`<case>-K-NNN`, unique
across DB *and* disk) the collision can't happen anyway, but `--fresh` +
`finalize_workspace` are what stop workspaces accumulating and make a fresh web
run reliable without manual `rm -rf`.
**How to apply:** any new "the API owns scoring/lifecycle" runner path should key
destructive workspace behavior off `--fresh`, never off `score_mode=manual`
alone (the CLI also uses manual). The fail-open branches now `return 1` (not 0)
so a run that built nothing is recorded `failed`, not falsely `awaiting_manual_score`.

## Preflight + visible enqueue failure turn silent no-ops into actionable errors — 2026-07-07

`RunService.create_run` now runs `_preflight` before persisting: empty
`executor_model` (non-dry-run) → `RunPreflightError` (HTTP 422); a `gateway/`
model with `GATEWAY_TOKEN` unset/placeholder → 422 too (token read from `.env`
via `config.read_env_file_value`, not a Settings field). And `_publish` now marks
the run `failed` with a reason when enqueue raises, instead of leaving it
`pending` forever. Consequence for tests: fixtures that create runs must pass a
non-`gateway/` `executor_model` (e.g. `"stub-executor"`) or `dry_run=True`, else
preflight rejects them.

## Deleting a run spans 3 stores with no DB cascades — 2026-07-07

`RunService.delete_run` (ADR-0007) removes, explicitly (no FKs/cascades exist):
`Validation`, `Attribution`, `Artifact`, `RunEvent`, `Run`, then the 1:1
`RunConfig`. Disk: `data/runs/<id>/` + `workspace/<id>/` via
`settings.workspace_dir` (new) — path-guarded to resolve under `repo_root` so a
`run_id` is never a free-text path. Container teardown is worker-only (API has no
docker socket, ADR-0002): `delete_run` enqueues `cleanup_run_container`. A
`running` run is refused (409) unless `force=True`.

## Claude Code gateway model names must match native alias resolution — 2026-07-07

Claude Code reads the generated `.claude/agents/*.md` `model:` frontmatter for
subagents. To make gateway-routed Claude Code behave like direct Claude Code, use
Claude Code's native aliases in agent frontmatter (`opus`, `sonnet`, `haiku`,
`fable`) and pin them with `ANTHROPIC_DEFAULT_OPUS_MODEL`,
`ANTHROPIC_DEFAULT_SONNET_MODEL`, `ANTHROPIC_DEFAULT_HAIKU_MODEL`, and
`ANTHROPIC_DEFAULT_FABLE_MODEL`. The gateway must expose the exact full model IDs
Claude Code resolves to (`claude-opus-4-8`, `claude-sonnet-5`,
`claude-haiku-4-5-20251001`, `claude-fable-5`). Nonstandard local aliases such as
`claude-opus-4.8-oauth` may work as legacy LiteLLM names, but they bypass Claude
Code's native alias/capability model and can produce misleading entitlement or
model-selection behavior.

## Roadmap checkboxes need a three-way done-ness distinction, not binary — 2026-07-07

Auditing `docs/roadmap-local-webapp.md` against real code (not memory or
assumption) surfaced that "done" splits into three states that a plain
checkbox can't express on its own: (1) fully done, (2) backend/model
implemented but no frontend action exists yet (e.g. case edit/delete/clone,
score submission, artifact indexing all have working endpoints/services but
no UI), and (3) declared/scaffolded but never actually exercised by any code
path (e.g. `RunState.draft`/`scoring`/`auditing` are valid enum members that
no transition ever assigns; `POST /api/runs/{run_id}/start` was never built
because the worker's poll loop makes it unnecessary).

**Why it matters:** checking a box because the enum member or database column
exists overstates progress; leaving it unchecked with no note understates it
and hides real backend work already done. Either mistake makes the roadmap
stop being trustworthy as a status source.
**How to apply:** when marking a roadmap/checklist item, grep for the concrete
code artifact that proves it (function name, enum assignment, route
decorator, frontend call site) before flipping `[ ]` to `[x]`. If the backend
exists but nothing calls it from the UI, leave it unchecked with a one-line
note naming what's missing, rather than checking it or leaving it silently
blank.

## Cross-process cancellation needs a DB-persisted signal, not shared memory — 2026-07-07

The API and worker run as separate Docker Compose processes/containers with no
shared memory, so `cancel_run` cannot directly reach into a running
`subprocess.Popen`. Instead, `Run.cancel_requested` is a DB column the API sets
when `state == running` (without touching `state` itself), and the worker's
drain loop in `_run_process_streaming` polls it every 0.5s, calling
`process.terminate()` when true. The worker itself then makes the terminal
`cancelled` transition via `RunService.mark_cancelled`, never the API.

**Why it matters:** if `cancel_run` had flipped `state` to `cancelled`
directly, a still-running worker would silently overwrite it back to
`completed`/`failed` via `mark_finished` once the subprocess exited — a race
between two processes writing the same row. Only one process should ever own
the terminal-state write for a running run.
**How to apply:** for any cross-process action on a live run/job when
API and worker are separate processes, signal through a DB column the owning
process polls, and let the owning process write the terminal state — don't
let the initiator guess the outcome.

## Worker log streaming needs threads per stream, not sequential reads — 2026-07-07

`subprocess.Popen` with separate `stdout`/`stderr` pipes will deadlock if you
read one stream to completion before touching the other (the child blocks once
the unread pipe's OS buffer fills). `worker.py` reads each stream in its own
thread into a shared `Queue`, and the main loop drains the queue into
`RunEvent` rows as lines arrive — this is what makes logs appear while the
process is still running instead of only at exit.

**Why it matters:** a naive `for line in process.stdout: ...` followed by
`for line in process.stderr: ...` works for tests with tiny output but silently
hangs on any real command that fills a pipe buffer.
**How to apply:** always drain multiple subprocess pipes concurrently (threads
+ queue, or `selectors`), never sequentially, when both streams matter.

## Docker-first local app wiring — 2026-07-07

The local web app is now practical to run under Docker Compose with the repo
mounted into the API and frontend containers. The API image must copy
`README.md` and `src/` before `uv sync` or Hatchling/uv editable builds can fail
while resolving the package.

**Why it matters:** the Docker path is the most stable way to keep macOS and
Linux behavior aligned for this project.
**How to apply:** prefer `docker compose build api frontend` and keep the
compose services pointed at the mounted repo root.

## Run config contract must stay aligned — 2026-07-07

`RunConfigCreate` needs to expose every flag consumed by the runner adapter
(`resume`, `dry_run`, prompt/model settings, and max steps) or preview and run
creation tests will fail even when the database model already has the field.

**Why it matters:** the runner adapter is the first real contract between UI and
benchmark execution.
**How to apply:** update the schema and the adapter together whenever command
flags change.

## How this template is wired for agents — 2026-06-22

The single source of truth for governance and skills lives in `.github/`; native
tool layouts (`.claude/`, `.opencode/`, `.agents/`, `.codex/`) are **generated** by
the `skills_sync` engine. Never hand-edit generated skill links or the region
between the `BEGIN/END GENERATED SKILLS` sentinels in adapter files — run
`make sync-skills` instead, and `make check-sync` to verify nothing is stale.

**Why it matters:** hand-edits to generated artifacts are silently overwritten and
break the CI drift gate.
**How to apply:** edit `.github/skills/*.md` (source) and regenerate.

## bench_run.py fails open without opencode — 2026-07-07

When the `opencode` binary is missing, `bench_run.py` does NOT fail: it prints a
manual-paste fallback and exits 0. A worker environment without opencode makes
runs look successful while the agent never executed. The Makefile guards this
with `command -v opencode || exit 1`; any new execution backend must guarantee
opencode is present (it is baked into `Dockerfile.runner`) or check explicitly.

**Why it matters:** this silent fallback was the root cause of "web runs succeed
but do nothing" while the CLI worked.
**How to apply:** treat opencode availability as part of the runner contract;
never run `bench_run.py` in an environment where its presence is unverified.

## Docker-out-of-docker bind mounts use host paths — 2026-07-07

The Celery worker runs in a container but launches runner containers through the
host daemon (`/var/run/docker.sock`). Bind-mount sources in `containers.run` are
resolved by the HOST daemon, so they must be host paths — the worker's `/app` is
meaningless there. Compose passes `HARNESS_LAB_HOST_REPO_ROOT: ${PWD}` for this.

**Why it matters:** using the container-local path silently mounts an empty or
wrong directory and the run fails confusingly.
**How to apply:** any DooD `volumes=` argument must come from
`settings.host_repo_root`, never from `settings.repo_root`.

## Env injection beats the mounted .env (setdefault) — 2026-07-07

`scripts/create_run.py:_load_env` populates `os.environ` with `setdefault`, so
env vars injected into the runner container win over the repo `.env` bind-mounted
inside it. `build_run_environment()` exploits this to rewrite
`localhost`/`127.0.0.1` gateway URLs to `host.docker.internal` without touching
the host `.env`.

**Why it matters:** it is the clean seam for host-vs-container networking; no
file rewriting or duplicated config needed.
**How to apply:** put per-environment overrides in the container env, keep
`.env` as the host-truth file.

## Trial condition_hash: identity vs provenance — 2026-07-07

`metadata.json` now carries a hashable trial condition (`provenance.py`). The
`condition_hash` (the experimental "arm" id) is computed over ONLY the
agent-facing harness (skills/agents/docs/hooks hashes), model settings
(temp/context/models), prompt-template hash, and case hash. Provenance-only
fields — `lab_git_commit`, `harness_git_dirty`, `runner_image` — are recorded
but deliberately EXCLUDED from the hash: they say *how* a trial was produced,
not *which arm* it is. So two reps of the same arm share a `condition_hash`
even across lab-code commits, while an ablation (one skill edited) flips it.

**Why it matters:** this is what makes runs groupable into cells/arms and makes
"a diff of two fingerprints = the description of the ablation" true.
**How to apply:** when adding a field, decide if it changes the *arm* (→ into
`compute_condition_hash`) or just documents provenance (→ manifest only).
Prompt hash uses the template (`prompt_source`), NOT the generated `prompt.md`,
which embeds per-run ids and would break arm stability.

## bench_run writes metadata before executing — 2026-07-07

`write_metadata` runs in phase "[1/5] Preparing", before the agent executes. So
`data/runs/<id>/metadata.json` (incl. the provenance manifest) exists even if
the run is later killed mid-execution — useful for verifying provenance without
paying for a full multi-step LLM run. But note: `--dry-run` returns BEFORE
metadata is written, so dry-run does not produce metadata.json.

## Validation runs in the worker, not the API — 2026-07-07

Objective validation executes a container (via the same `RunExecutor` as runs),
so it must run where the docker socket is mounted: the **worker**. The API
process has no socket (ADR-0002), so `POST /api/runs/{id}/validate` enqueues a
`validate_run` Celery task and the frontend polls `GET /validation`. Running it
inline in the API fails with `DockerException: ... /var/run/docker.sock ... No
such file or directory`.

**Why it matters:** any feature that spawns a container belongs in the worker.
**How to apply:** API endpoints that need docker must enqueue, never call the
executor inline.

## Runner container: sh -c, not sh -lc; pygame via uv pip — 2026-07-07

Validation commands run with `sh -c`. A login shell (`sh -lc`) re-sources
profile and drops the runner image's `/opt/venv/bin` from PATH, so `python`
resolves to a system interpreter without the installed deps (pygame not found).
Also: the uv-created `/opt/venv` has no `pip`; install extra runtime deps with
`uv pip install --python /opt/venv/bin/python <pkg>`, not `/opt/venv/bin/pip`.

**Why it matters:** both bugs made the Tetris smoke fail spuriously.
**How to apply:** run in-container commands with `sh -c`; manage the venv with uv.

## Composite score gates subjective by objective — 2026-07-07

The multidimensional score keeps objective/cost/effort/subjective separate and
only combines them in `composite`, which caps the manual functional score at 1.0
when objective validation FAILED (a high manual score is not credible when the
code does not run). Dimensions are never collapsed before that.

**Why it matters:** preserves the signal an ablation needs; prevents a
misleading single number.
**How to apply:** new metrics go in as their own dimension; only `compute_composite`
merges them, explicitly.

## opencode log signals for harness attribution — 2026-07-07

The opencode step logs (`data/runs/<id>/steps/step-*.log`) expose tool/file
behavior in a stable `key=value` format: `permission=<read|edit|bash> pattern=…`
(tool + target), `"shell tool using shell"` (shell), `agent=<mode>` (build /
plan / title / undefined). There is NO explicit skill-invocation event — skills
are injected into context, so file-based skill attribution is a lower bound
(only counts `SKILL.md` reads). `"touching file"` is the file-watcher and fires
on reads too, so it is NOT an edit signal — edits come only from
`permission=edit`.

**Why it matters:** these are the ground-truth signals for pillar 3 attribution;
misreading "touching file" as an edit wrongly flags read-only governance docs.
**How to apply:** count edits from `permission=edit`; treat skills_consulted as a
documented lower bound; parse step logs, fall back to opencode.log.

## SWE-bench generic runtime runs only a narrow slice — 2026-07-20

The image-free runtime (clone → `uv pip install -e .` on Python 3.11 → pytest the
F2P/P2P nodeids, ADR-0009) works for a *narrow* slice, proven via
`make swebench-selftest` (golden-patch, zero tokens). Three hard boundaries found
while curating SWE-bench Verified: (1) **Py3.11** — pre-3.11 code fails at import
(every `psf/requests` Verified instance vendors an old urllib3 doing
`from collections import Mapping`); prefer 2022+ releases. (2) **Standard pytest
nodeids only** — F2P ids with spaces/params like `test_x[NOT AND SELF]` don't
match under the generic runner (`pytest-10081/10356` fail; clean-nodeid
`pytest-10051` passes). (3) **pytest-collectable only** — `sympy` names tests by
bare function (`test_homomorphism`) for its own runner; `pylint` tests fail
collection. Reliable slice today: modern `flask` + `pytest` (7.x). Validated set
lives in `docs/swebench-curated-set.md`.

**Why it matters:** picking instances by "looks pure-Python" wastes runs; most
Verified repos (django/sympy/matplotlib/sklearn/pylint) don't run here.
**How to apply:** never trust an instance until `make swebench-selftest` passes;
`fetch_instance` searches both Lite+Verified (`swebench_client._DATASETS`); for
dataset-free objective tasks author a `build_from_scratch` case with exit-code
`validation_commands` (see `benchmarks/cli-textstats/`).

## Harness is an executable dimension via per-CLI adapters — 2026-07-20

The New-run `harness` field used to be cosmetic: the runner always called
opencode (`build_opencode_cmd`), and the runner image installs only opencode.
codex/claude are now real harnesses (`build_codex_cmd` / `build_claude_cmd`,
dispatched by `build_harness_cmd` in `bench_run.py`, threaded via `--harness`).
Key facts: the workspace path lives in the *prompt* (`workspace/<run_id>/`), so
all harnesses share `cwd=REPO_ROOT` — only the CLI differs. codex/claude run on
the **host** (their CLIs + native auth live there, not in the container), so they
require the subprocess backend; a `RunService._preflight` guard rejects them
under the docker backend. The gateway model dropdown (`gateway/…`) doesn't map to
native Codex/Claude ids, so `_native_model` ignores it → CLI default.

**Why it matters:** adding a harness = a builder + install location, not a whole
new runner; and codex/claude only work in host mode (make app-worker-host /
app-api-host), never under `make app-up`.
**How to apply:** see `docs/harness-adapters.md`; builders are unit-tested in
`tests/harness_lab_app/test_harness_adapters.py`; real headless completion on
tetris still needs first-run verification per environment.

## Continuous-improvement cycle: audit findings → GitHub issues — 2026-07-20

`scripts/lab_improve.py` (`make improve RUN=<id>`) closes the lab's feedback loop:
it reads a run's LLM audit (`audit.md` + `audit_findings.json`, verdicts
useful|redundant|improve|unknown), runs a *dedicated* LLM synthesis pass
(reusing `bench_audit.call_gateway`) to turn the "improve" signals into concrete
GitHub issue proposals, and files them via `gh` on the host. Two steps: default
dry-run writes an editable `data/runs/<id>/improvements.json` (+ `.md` preview);
`CREATE=1` files the issues (idempotent — created URLs are written back).
Approval = running the create step. Routing (`route_repo`, editable in the JSON):
skills/governance-docs/subagents/hooks → `IMPROVE_REPO_TEMPLATE`
(marcosdh1987/ml-python-base); process/harness-hint → `IMPROVE_REPO_HARNESS`
(marcosdh1987/harness-engineering-guide).

**Why it matters:** this is how lab experiments become durable improvements to
the template/harness repos, not one-off observations.
**How to apply:** needs an audit first (`make bench-audit` or Run audit button) +
`gh auth` + gateway; see `docs/improvement-cycle.md`; pure fns unit-tested in
`tests/harness_lab_app/test_lab_improve.py`.

## SWE-bench Pro lane: image-based hard cases — 2026-07-21

The Pro lane (`docs/design-swe-bench-pro-lane.md`, golden-patch-proven on an
ansible instance) has four non-obvious gotchas learned the hard way:

1. **Pro images default to a bash entrypoint** (upstream issue #6). Passing
   `bash -lc …` as CMD runs `bash bash …` and dies silently — always use
   `--entrypoint bash` and pass only `-lc <script>` as args.
2. **`dockerhub_tag` is not the real tag.** The published tag is *derived*
   (`helper_code/image_uri.py` port in `swebench_pro_client.image_uri`):
   `-vnan` stripped, element-web special-cased, truncated to 128 chars.
3. **Pro list columns are Python-repr strings**, not JSON (`['a', 'b']` with
   single quotes) — parse with `ast.literal_eval`, not `json.loads`.
4. **The gold patch rides the normalized spec for self-tests but must never
   reach `metadata_json`/`case_spec.json`** — that file is bind-mounted where
   the agent can read it (oracle mode leaks test_patch on purpose; the gold
   patch would void the bench).

**Why it matters:** silent zero-scores (gotcha 1) look identical to "agent
failed"; a wrong tag (2) looks like a missing image; and (4) is a validity
threat to every Pro result.
**How to apply:** provisioning/validation live in `scripts/swebench_pro_common.py`
(worker-side in the web lane: runner has no docker socket; validation routes via
the `local` flag in `build_validation_spec`); smoke with
`make swebench-pro-selftest INSTANCE=instance_…`. Images are amd64-only — dev
smokes on Apple Silicon run emulated; real benches belong on the amd64 server.

## Operating the lab: restarts kill in-flight runs — 2026-07-22

`docker compose restart worker` aborts any run mid-flight (the run is marked
failed with "Worker restarted while the run was in flight; retry to resume").
The worker is deliberately concurrency-1, so one active run is always possible.

**Why it matters:** deploying lab code changes while an experiment runs silently
costs a run (and its tokens).
**How to apply:** before restarting api/worker, check the runs list for
`running`/`pending` states (`GET /api/runs`) — restart only when empty. A run
killed this way is recoverable with the Retry button.

## LM Studio JIT auto-load serves the model's *default* context — 2026-08-10

The context length you set by hand in LM Studio applies to the model **you
loaded**. A request for a model that is not loaded triggers a JIT load using that
model's **default** window (often 8k) and ignores your setting. Nothing upstream
looks broken: LiteLLM answers normally and `/v1/models` lists the model.

The harness system prompt is **~13.7k tokens before the first user turn**, so
under an 8k window llama.cpp rejects every call *before evaluation* with
`n_keep: 13777 >= n_ctx: 8192` in ~0.4s. Run `tetris-v1-K-020` burned 4432 steps,
2.5 hours and a 12 MB log this way, and the failures surface as generic stream
errors.

Two related traps found alongside it. **Alias identity is the full parameter set,
not the target.** `/model/info` shows `lmstudio-qwen3.6-35b` and its `-build` twin
pointing at the same LM Studio model but with different sampling (0.7/top_p 0.8
against 0.2/top_p 0.9 + penalties + `max_tokens` 4096) — two real arms. Meanwhile
`claude-opus-5`, `-oauth` and `[1m]` are byte-identical in everything the gateway
exposes. Judge by params, never by name. **And LiteLLM returned an identical
`chatcmpl` id + `created` for two identical requests**, which means response
caching — poison for a benchmarking lab if it is on.

**Why it matters:** the symptom (opaque stream errors, instant failures) points at
the network or the harness, not at a context setting on a machine that reports
itself healthy.
**How to apply:** load the model explicitly with the window you want before a run,
or raise its default. The lab now measures the *served* window with a real
oversized prompt — Settings -> Model catalog -> **Check context**, and run
creation refuses a proven window under `MIN_CONTEXT_TOKENS` (32768). Both fail
open, so only a provable shortfall blocks. See
[ADR-0020](../docs/adr/0020-context-window-preflight-and-alias-merge.md) and the
"JIT auto-load trap" section in `docs/local-model-runtime-config.md`.

## A silent local model is prefilling, not stuck — 2026-08-13

Measured against the box serving `qwen/qwen3.6-35b-a3b`: a trivial prompt returns
its first token in **0.2s**, the harness's ~24k-token prompt with tool schemas in
**318s**. Time-to-first-token is dominated by prompt processing and scales with the
prompt, so a big local model produces *no output at all* for minutes at the start of
every turn while looking, from outside, exactly like a wedged one.

The lab had one 600s silence clock (`run_stall_seconds`, added for the hung-pygame
run `K-034`), which sat below that prefill time. Every 35B run therefore died at its
first turn — `K-040/049/050`, all at `message=stream` → `llm runtime selected` →
silence → exit 137 at exactly 600s — and was filed as the *agent* wedging the
harness. The 9B, whose worst gap was 76s, always passed. Event counts make the shape
obvious: 99/221/126 events for the 35B runs against 1727–3791 for the 9B.

Found alongside it: **opencode retries a failing stream forever.** `K-029` logged 123
consecutive `AI_APICallError: Cannot connect to API` over 70 minutes and was recorded
as `Run timed out after 7200s` — i.e. as nemotron attempting the case. That pattern
explains the block of eight 130–157 minute "timeouts" from `K-018` to `K-030`.

**Why it matters:** both defects blamed the model for a lab problem *and* fed the
leaderboard, so "the big model regressed" was an artifact of the harness. A
benchmark that mislabels its own failures is worse than one that records nothing,
because it looks authoritative.
**How to apply:** the watchdog now picks its budget from what the run was doing when
it went quiet — `run_model_wait_seconds` (2400) while a request is in flight,
`run_stall_seconds` (600) otherwise — and gives up after `run_max_stream_errors` (5)
back-to-back provider failures. Only agent-caused silence counts as an attempt. When
a local model looks hung, check GPU load before the harness: a prefilling model pins
it, a stuck tool call does not. See
[ADR-0022](../docs/adr/0022-watchdog-splits-model-wait-from-agent-stall.md).

## A green check on a Dependabot PR can mean "never tested"

`docs-quality-guardrails.yml` has two jobs: a Python gate (`make check`) and a
frontend gate (`make check-frontend`). **Neither builds a container.** So a
base-image PR — nginx, node, python — reports ✅ without a single line of it being
executed. The check is measuring the absence of a test, not the presence of a pass.

Building `Dockerfile.frontend.prod` by hand found a bug that had been live the whole
time and had nothing to do with any bump: the `HEALTHCHECK` probed
`http://localhost/`, which resolves to `::1` first inside the container, while
`deploy/nginx.conf` declares only `listen 80` (IPv4). Every probe got
`ECONNREFUSED`, so the production frontend container reported `unhealthy` for its
entire life while serving traffic perfectly. It reproduces identically on
`nginx:1.27-alpine`, so it predates the upgrade — CI simply had no way to see it.

The same sweep showed Dependabot's one-major-per-PR split creating *unmergeable*
PRs: `@vitejs/plugin-react@6` requires `vite@^8` and `vite@8` breaks
`plugin-react@4`, so each PR fails `npm ci` alone and only passes together. Five
frontend PRs also share one `package-lock.json`. Merging them one at a time is not
a slower path to the same place — for two of them it is not a path at all.

**Why it matters:** dependency PRs are where review attention is thinnest and the
green check does the most work. When the gate does not cover the artifact being
changed, the check is decoration, and the temptation is to batch-merge on it. The
upgrade was also genuinely security-relevant — `npm audit` went from 4 findings
(2 high on postcss/nanoid, 2 moderate on react-router including an open-redirect
CVE bypass) to 0 — so the cost of getting it wrong was real in both directions.

**How to apply:** before merging anything that changes a Dockerfile, build the image
and exercise it (`/`, a client-side route, a hashed asset, and the healthcheck) —
`make check` will not. When two Dependabot PRs fail on each other's peer ranges,
stop trying to sequence them and replace the set with one verified branch. Base
images now move only with the gate that verifies them, enforced by `ignore` rules in
`.github/dependabot.yml`; see
[ADR-0024](../docs/adr/0024-base-images-track-ci-verified-toolchains.md).

## Continuing a run: a child run was the cheap design, and two bugs sat next to it

**What happened:** adding "continue a finished run with a new prompt" looked like
it needed runner support — step offsets, merged usage, re-opened artifacts. It
needed none. Modelling the continuation as a *new run* whose workspace is copied
from the parent means it owns its own `data/runs/<id>/`, so there is nothing to
offset or merge. Two flags that already existed did the rest: `--resume` makes
`validate_workspace` keep the seeded tree, and `provision_workspace`
short-circuits on an existing `.git`, so a rehydrated SWE workspace is not
re-cloned. The only genuinely new code was one `shutil.copytree` and a prompt
header telling the agent the inherited workspace is its own.

The one place the lane mattered: a curated case (`RUNS_FILES`) resolves its
prompt from `benchmarks/<case>/`, ignoring `case_spec.json` entirely — so a
continuation had to override `_prompt_source.md` *after* the lane branch, not
inside the DB branch.

Two adjacent defects surfaced while wiring it, both in the same shape — a
`RunConfigCreate` rebuilt field by field, silently dropping the fields added
later:

- `tasks._config_payload` omitted `skills_disabled`/`skills_extra`, and
  `execute_run` overwrites `run.command` with what it rebuilds. **The skills
  ablation never reached the container.** The stored preview command had the
  `--disable-skill` flags; the executed one did not. Every ablation arm run
  before 2026-08-24 measured the full harness.
- `_resume_config` (retry) and `clone_run` dropped the same two fields, so a
  retry or clone quietly changed arms.

**Why it matters:** ADR-0011's whole point is that an ablation is a distinct
condition. A silent drop does not fail — it produces a plausible number for a
condition that was never run, which is worse than an error.

**How to apply:** when a feature can be expressed as a new row rather than a
mutation of an existing one, price that first — immutable artifacts make most of
the hard parts disappear. And treat every hand-rolled `RunConfigCreate(...)` as a
place a future field will be dropped: `_config_payload`, `_resume_config`,
`clone_run` and `continue_run` must all be updated together when the schema
grows. See [ADR-0026](../docs/adr/0026-run-continuation-as-child-runs.md) and
[guide-run-continuation.md](../docs/guide-run-continuation.md).

<!-- Add new learnings above this line -->

# Current context

## Project

AI SDLC Lab — a benchmarking harness for AI-assisted development.
Goal: measure which harness + model + prompt combination produces the best result,
at what cost, with what number of iterations. Inspired by SWE-bench methodology:
frozen task + repo/base commit + harness strategy + validation + score + cost.

## Active focus

Building the Docker-first local web app for Agentic Harness Lab.
Execution backend is now Celery + Redis (broker only) with **one Docker
container per run** (SWE-bench style, ADR-0002): `Dockerfile.runner` bundles
opencode + conductor plugin + uv; the Celery worker (compose service with the
docker socket mounted, concurrency 1) launches sibling runner containers that
bind-mount the host repo rw and reach the host LiteLLM/LM Studio via
`host.docker.internal` (env rewritten from `.env` by `build_run_environment`).
Container logs stream into `RunEvent`; cancel stops the container; retry
persists `resume=True` and re-publishes; orphaned `running` runs are
reconciled to `failed` at worker startup. The old polling `worker.py` was
removed. Web runs are `score_mode=manual` only and finish in
`awaiting_manual_score`.
Pending validation: a real (non-dry-run) Tetris run from the UI against a live
LM Studio + LiteLLM gateway with a populated `.env` (dry-run E2E through
API → Redis → Celery → runner container verified 2026-07-07).

SWE-bench case type landed 2026-07-08 (ADR-0009, branch `feature/web-ui`):
generic image-free runtime (clone + `uv pip install -e .` + pytest) with a
per-repo `_ENV_SPECS` pin map in `scripts/swebench_common.py`. Import a Case with
`make swebench-import INSTANCE=pallets__flask-4992` (fetches HF datasets-server,
stdlib only). Provisioning: worker writes `data/runs/<id>/case_spec.json`
(`tasks._write_case_spec`); `bench_run.py` takes a repo-task branch (clone @
base_commit, apply test_patch, commit, install) bypassing tetris `RUNS_FILES`.
Objective validation runs `scripts/swebench_validate.py` (reinstall archived repo
+ F2P/P2P → `SWEBENCH: resolved=…`); `build_validation_spec` now targets the
archived `data/runs/<id>/workspace` (also fixes Tetris under `--fresh`).
Verified E2E in the runner container with **zero tokens** (golden→resolved;
no-agent→`resolved=False F2P=0/1 P2P=18/18` rc=1). Phase 3 (real open-source
agent run) is the next step. Prove the harness first:
`make swebench-selftest INSTANCE=pallets__flask-4992`.

Usability/reliability pass landed 2026-07-07 (branch `feature/web-ui`): fixed the
"set RESUME=1" stale-workspace trap (managed `--fresh` runs + `finalize_workspace`,
ADR-0006), loud preflight (empty executor / placeholder gateway token → 422) and
visible enqueue failure (broker down → run `failed`, not stuck `pending`),
auto-incremental `<case>-K-NNN` run ids + "Run again"/clone, run **delete**
(DELETE `/api/runs/{id}` cascading DB+disk+container, ADR-0007), and a frontend
overhaul (react-router deep-links, collapsible sidebar, state-color chips, dark
mode, per-run cancel/clone/delete). `make check` 145 pass; frontend `vite build`
green. `make app-reset-db PURGE=1` now also clears disk. Still needs a real live
Tetris run from the UI to confirm end-to-end.

Lab-evolution pass landed 2026-07-08 (branch `feature/web-ui`, ADR-0011), four
shippable phases, all verified (`make check` 204 pass, `make check-sync` clean,
frontend `tsc && vite build` green):
- **F1 SWE-bench en la UI**: `POST /api/cases/import-swebench` (inline HF fetch
  via new adapter `swebench_client.py`; scripts re-import from it), import
  panel + type badges/filter + F2P/P2P counts in Cases.tsx. SWE-bench cases
  remain DB-only (a `make app-reset-db` removes them — re-import).
- **F2 resultados legibles**: audit renders as markdown (react-markdown+gfm),
  `bench_audit.py` second JSON-only pass → `audit_findings.json` →
  `Audit.findings_json` (migration in db.py) → "Harness catalog" panel joining
  metadata (available) + attribution (consulted) + LLM verdicts
  (útil/redundante/mejorar); LogViewer parses logfmt with level filters;
  Artifacts panel is real (list + viewer; worker now auto-indexes artifacts
  post-run so RunDetail can read metadata.json).
- **F3 ablación por corrida**: `RunConfig.skills_disabled` → `--disable-skill`
  → bench_run regenerates native views + adapter blocks via skills_sync with
  the filtered list (restore-at-start + atexit restore); condition_hash gains
  the sorted list only-when-non-empty; Skills checkbox section in the run form.
- **F4 staged skills**: `StagedSkill` DB row + `data/skills_staging/` (git-
  ignored), Skills page (create/edit/archive/delete/promote — promote writes
  `.github/skills-external/` + sync, never commits), "Experimental" group in
  the run form → `--extra-skill`; staged content hash flips condition_hash.
  Guide: `docs/guide-skill-validation.md`.
Pending E2E (needs live gateway/worker): an ablated run from the UI verifying
symlinks/OPENCODE.md during execution + clean `git status` after (requires the
runner image rebuilt with jinja2); an audit run producing findings verdicts in
the catalog panel.

SWE-bench auto-scoring landed 2026-07-09: first real run
(pallets__flask-4992-K-001) RESOLVED the issue in step 1 (F2P 1/1, P2P 18/18)
but drifted for 9 more Tetris-framed steps. Now: SWE continuation prompts,
objective per-step stop (`swe_step_evaluation` breaks on resolved), worker
auto-score (resolved→10/else→0, binary like official SWE-bench) completing the
run without manual score, Re-validate retro-scores parked runs, SWE-aware
Validation panel + post-run retry polling in RunDetail. K-001 can be completed
by pressing Re-validate once the worker restarts with this code.

Repository harness audit landed 2026-09-09 (ADR-0054, branch
`feat/end-to-end-testing-updates`): the guided audit is now four steps —
point/pin → **Harness audit** → one case → run an experiment. Step 2 is an
LLM reading (`repository_audit_service.py`, `RepositoryAudit` row, worker task
`audit_repository`, `GET/POST /api/targets/{id}/repository-audit`) of the tree at
the pin: rules files, skill/agent heads, commands, `.claude/settings.json` hooks,
pre-commit/husky, CI, Makefile targets, manifests, README → fixed JSON
(summary, harness_verdict, components, language, standards, gaps, 3-5 use cases)
rendered to Spanish markdown by the lab. The wizard gate (`next_step` in
`evidence_json`) is deterministic: `unsupported_runtime` (no Python/Node by
marker or extension census) ends the audit; `no_harness` ends the primary flow
with one secondary "continue anyway"; `continue` goes on. Step 4 offers
`native` alone (subject validation) or `native` vs `injected` (ml-python-base);
`bare` is not offered inside a target (ADR-0049). Client report gains a
"harness, as read by the auditor" section. Pending: a live audit against a real
gateway/Claude CLI from the UI.

Injected-arm fix + LLM-call tracing landed 2026-09-09 (ADR-0055, branch
`feat/end-to-end-testing-updates`). Every `injected` run died in provisioning
with "Adapter file is missing the generated-skills sentinels": ADR-0049 masks
the lab's `CLAUDE.md` inside the runner, and `inject_harness` reads that same
file to splice into. Fix: `project_harness(..., adapter_root=)`, the worker
stages the registry's adapter files into `data/runs/<id>/harness_source/`
(`tasks._stage_harness_adapters`), `bench_run.inject_harness` prefers it, and
`_require_spliceable` refuses a masked/unsynced source by name before copying.
Separately, `llm_transport.chat` now takes `trace_kind`/`trace_metadata` and
emits one Langfuse trace per in-process completion via
`observability/call_export.py`: kinds `repository_audit`, `case_draft`,
`experiment_audit`, `judge`, `summary`, tagged `llm-call`, one session per kind,
2s export timeout, failures traced at ERROR. Langfuse export events on a run were
also downgraded from `error` to `warn` — an unreachable Langfuse costs a trace,
not a run. Not yet exercised against a live Langfuse.

## Constraints and decisions in force

- The database is the source of truth for cases and runs (ADR-0025). The CLI lane
  (`runs.yaml`, `make bench-start/bench-close/bench-results`, `results.csv`) still
  exists but is legacy; do not treat it as authoritative.
- Scoring follows ADR-0038: web runs use `score_mode=manual`, but a case with
  executable validation (SWE-bench, SWE-bench Pro, a target repo with a gate, a
  custom case with `validation_commands`) scores itself; a case with a
  `scoring_rubric` is LLM-judged; only rubric-less, non-objective cases stop in
  `awaiting_manual_score`.
- One LLM transport for every in-process call (ADR-0043,
  `src/harness_lab_app/llm_transport.py`): gateway when `GATEWAY_BASE_URL` +
  `GATEWAY_TOKEN` are set, else the Claude Code CLI when a Claude credential is set.
  Gateway wins when both exist. Either credential path alone runs the lab end to end.
- Runs can target a registered repository at a pinned commit (ADR-0029/0042): the
  engineer clones into `data/checkouts/<target_id>` with the command the UI shows;
  `harness_source` (`lab_root` | `native` | `injected`) is a per-run condition
  (ADR-0031); the audit judges against the run's own governance surface (ADR-0036).
- `.env` is bind-mounted and read per request / per run — editing it (or Settings)
  needs no restart.
- Per-run artifacts live under `data/runs/<run_id>/` (git-ignored).

## Current run state

Tracked in the web UI; no CLI table maintained.

## Scientific-lab direction (agreed 2026-07-07)

The lab is being shaped into an instrument to evaluate the *engineering harness*
(skills + docs + hooks + agents) as the **independent variable**, across
cases/prompts/models incl. future SWE-bench. Vocabulary: case (frozen task),
arm/condition (harness fingerprint + prompt + model + params), trial (one run),
cell (case×condition, N reps), experiment (matrix varying ONE factor). Two
method pillars: causal attribution needs **ablation** (toggle one factor,
re-run, compare distributions), not observation; and local models at temp>0
mean **statistical** reproducibility (N reps, mean±spread), not deterministic
replay.

Pillar order chosen: (1) provenance/fingerprint ✅ DONE, (2) objective
validation + multidimensional score ✅ DONE (ADR-0004), (3) structured harness
attribution ✅ DONE (ADR-0005), (4) ablation/experiment runner (NEXT). See
ADR-0003/0004/0005.

Pillar 3 delivered: `attribution.py` parses a run's opencode step logs into a
machine-readable `Attribution` (tools read/edit/bash/shell, files read/edited,
governance-doc read coverage, bash + agent-mode histograms), cross-referenced
with the available skills from metadata. Pure log parsing (no container) → runs
inline in the API and auto-indexed after every run (success or fail).
`GET/POST /api/runs/{id}/attribution`, panel in RunDetail. Honest limit: skill
*invocation* isn't file-observable (skills are context-injected), so
skills_consulted counts only SKILL.md reads (a lower bound); documented in the
`skills_note` field, not faked. Verified on K-001: read 5/8 governance docs,
edited only PLAN.md + main.py, python×16 self-tests, agent mode build.
Fixed a real bug: opencode's `"touching file"` is the watcher (fires on reads),
not an edit — edits come only from `permission=edit`.

Pillar 2 delivered: a `Validation` per run (`validation.py`,
`validation_service.py`, `models.Validation`) with separate objective / cost /
effort / subjective dimensions + an explicit `composite` gated by the objective
floor. Objective validation runs the case's `validation_commands` (shell with
`{workspace}` placeholder) in a runner container via the executor — auto after a
successful run and on demand via `POST /api/runs/{id}/validate` (enqueued to the
worker, which owns the docker socket). Tetris uses a headless smoke
(`BENCH_SMOKE=1 SDL_VIDEODRIVER=dummy ... python {workspace}/main.py`); the
runner image now bundles pygame. Verified E2E on the real K-001 Tetris:
objective pass, cost 206961 tokens, effort 8 steps, composite 7.0 after a
manual functional score.

Git hygiene: run artifacts are no longer tracked — `data/runs/**` is git-ignored
(only `.gitkeep`/`.gitignore` remain); operational run state lives in the DB and
on disk, never in git. The CLI (`runs.yaml`/`results.csv`) is kept as a
deprecated legacy path; the scalable target is the DB-only web server.

Provenance delivered: `src/harness_lab_app/provenance.py` +
`bench_run.write_metadata` now write a hashable trial condition into
`metadata.json`: `condition_hash` (arm id), `harness_fingerprint`
(skills-lock hashes + agents + governance docs + hooks + opencode/plugin
versions + rolled `harness_hash`), `model_condition` (temp/context/alias),
`prompt_hash`, `case_hash`, `lab_git_commit`, `harness_git_dirty`,
`runner_image`. Verified on a real run: K-002 metadata carried a full manifest.

## Open threads / next steps

- **3D game lane (ADR-0078, 2026-11-13, uncommitted).** A case may name its own
  `runner_image` / `sandbox_image` / `play_command` (`case_profile.py`); `make images-3d`
  builds `harness-lab-runner-godot` + `harness-lab-sandbox-godot` (Godot 4.7.2, Xvfb,
  llvmpipe); the run page gains a **Play** tab (noVNC desktop that starts the game).
  First case `chess3d-v1`: GDScript harvester + 39 python-chess checks on run-seeded
  probes, calibrated through the real worker in the hardened lane (empty 0/39, weaker
  24/39, reference 39/39; reference in git-ignored `data/reference/chess3d-v1/`). Its
  tamper guard goes green once the grader is committed. Next: a real frontier run
  (and a small model) on `chess3d-v1`; then the racing case (track by seed, recorded
  inputs, AI rivals on a held-out track) and the open-city case (OpenStreetMap
  extract, held-out neighbourhood by seed, building-height raycasts). Guide:
  `docs/guide-3d-game-cases.md`.

- **Per-step reward (ADR-0076, 2026-11-12, uncommitted).** RL-inspired: the runner
  snapshots the workspace after every step, the worker regrades each snapshot with the
  case's checks, five `step_reward` scores, per-step credit, and `make prune`
  (leave-one-out skill ablation with a non-inferiority margin). Guide:
  `docs/guide-step-reward.md`. Next: run a first real `make prune` on 3+ cases per
  model tier; regrade target-repo diffs per step; per-step cost for opencode;
  consider `governance_read_coverage@v3` counting shell reads; decide separately
  whether `steps_to_resolve` should ever stop the loop (kept out on purpose).

- **After `fix/measurement-honesty` merges (ADR-0073), continue with items 6–10** of
  `docs/plans/2026-11-measurement-honesty-follow-ups.md`: judge vs checks agreement
  and per-arm exclusions (6), Wilson relabelled "on these cases" plus a case-clustered
  arm interval (7), repetition planning by simulation, cases vs repetitions (8, the
  most valuable), repeated repository audits with merged cases (9), and
  `skill_adherence` crossed with the catalog (10). Source: Federico Trinidad's
  "Harness Lab bajo prueba" (2026-11-06), reproduced with the lab's own code.

- `make check` on `feat/end-to-end-testing-updates` fails 2 pre-existing tests in
  `test_prompt_variants.py`: `benchmarks/cli-textstats/prompt_harness.md` and
  `benchmarks/tetris-v2/prompt_harness.md` drifted from preamble + default (a
  blank line). `make prompts` regenerates them; not touched by the audit work.
- Run the repository harness audit live (needs a gateway or Claude CLI credential
  and the worker up) and read a real report: prompt length on a repository with
  25 skills, and whether the JSON contract holds on the default auditor model.
- Re-run the `native` vs `injected` experiment now that ADR-0055 unblocks the
  treatment arm; the earlier attempt lost its comparison to that crash and its
  `native` arm read only 2 of 18 governance docs on 1 repetition (exploratory).
- Consider a launch-time preflight that refuses an `injected` arm whose adapter
  files are not spliceable, so a crash like ADR-0055's cannot burn a paid control
  arm before the treatment fails.

- **Landed (ADR-0043):** Claude-native LLM transport — a Claude subscription alone
  runs the lab end to end; the audit, improvement synthesis, SWE-Pro judge, case
  summary and DeepEval metrics all go through `llm_transport.py` (gateway first,
  Claude CLI second). Plan: `docs/plans/2026-09-claude-native-llm-transport.md`.

- Pillar 4 (ablation/experiment runner) — NEXT: matrix case×arm varying ONE
  harness factor, N reps, compare distributions using `condition_hash`
  (ADR-0003) as the grouping key, with the multidimensional `Validation`
  (ADR-0004) + `Attribution` (ADR-0005) as the per-trial outcome features.
- Deeper skill attribution: have the harness emit skill-invocation events so
  `skills_consulted` stops being a SKILL.md-read lower bound.
- Add a `bugfix-with-tests` validation_commands (pytest) to exercise the "tests"
  strategy (only Tetris smoke exists so far).
- The web app should index `metadata.json` and group runs by `condition_hash`
  (foundation for the Phase 7 comparison surface).
- DB-only migration: make the runner read run definitions from the DB so
  `runs.yaml`/`results.csv` can leave git entirely (currently kept as legacy CLI
  state the runner still needs).
- For rigorous trials, commit the harness first so `harness_git_dirty=false`.
- Tail `data/runs/<run_id>/steps/*.log` when available (still open in Phase 4).
- Add richer run detail tabs for artifacts, diff, score, and audit (Phase 5).
  Backend artifact indexing already exists (`ArtifactService.index_run`); the
  gap is entirely frontend (no artifacts list/download UI yet).
- Expand the case wizard into edit/delete/clone flows (backend endpoints for
  all three already exist in `CaseService`/`api.py`; only frontend buttons
  are missing).
- Add a frontend login flow and session gating (backend `POST
  /api/auth/login`/`logout` exist; still the main open item under Phase 3).
- `docs/roadmap-local-webapp.md` was fully audited 2026-07-07 against actual
  code/tests/frontend — every checkbox now reflects a verified implementation
  state, with notes distinguishing "backend done, no UI yet" from "declared
  but never reachable" from "not started." Keep it checked as the MVP grows.
SWE-bench step 9 verification: Tests passing, issue resolved.
MEMORY: SWE-bench continuation step 4 completed successfully for RUN_ID=instance_ansible__ansible-0fd88717c953b92ed8a50495d55e630eb5d59166-vba6da65a0f3baefda7a058ebbd0a8dcafb8512f5

Target tests in test/units/plugins/lookup/test_password.py::TestParseContent all pass:
- test_empty_password_file
- test
- test_with_salt
- test_with_salt_and_ident

The _parse_content function in lib/ansible/plugins/lookup/password.py already returns 3 values (password, salt, ident) and correctly parses password file content with optional salt and ident parameters. No code changes were needed.

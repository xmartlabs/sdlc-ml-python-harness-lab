# Plan — Open-source readiness and batch runs

**Status:** implemented (2026-08-16) — all five phases landed; see
[ADR-0023](../adr/0023-batch-run-execution.md) for the batch decision record.
**Date:** 2026-08-16
**Scope:** publish this repository as the reference lab for the `ml-python-base`
harness, and add multi-select batch execution.

## Framing

The repository is published as **the lab of the `ml-python-base` harness** — not as a
harness-agnostic platform. That is a deliberate narrowing: it closes the improvement
cycle (run a case → audit → sanitized issue → improve the skill → re-measure) as a
single coherent story, and it defers the structural work of turning the harness into a
data input.

The consequence to accept openly: today `repo_root` defaults to `Path.cwd()`
([config.py:13](../../src/harness_lab_app/config.py#L13)), so **the harness under test
is the repository the lab runs from**. Selecting `harness` in the UI picks which CLI is
invoked, not which governance surface is loaded. Testing a *different* harness still
requires cloning it and running the lab inside it. This plan does not change that; it
makes the current, honest story publishable. Genericizing the harness is tracked
separately as a follow-up (see "Explicitly out of scope").

## Decisions taken

| Decision | Choice | Rationale |
|---|---|---|
| License | **Apache-2.0** | Explicit patent grant; de-facto standard for agent tooling. |
| Multi-run shape | **Cartesian batch** | `POST /api/runs/batch` over lists. `suite.yaml` becomes a producer of batches later, not a parallel path. |
| Naming | **Project only** | `pyproject.name` → `agentic-harness-lab`. `src/ml_python_base/skills_sync` keeps its name — it is genuinely the template engine, and renaming breaks the `template_sync` contract in [registry.toml](../../adapters/registry.toml). |

## Phase 1 — De-hardcode identity

Nothing here is a security issue. The LAN addresses are RFC-1918 and non-routable; they
are a polish problem (a homelab leaking into product copy), not an exposure.

### 1.1 Repository slugs → settings

`marcosdh1987/*` is hardcoded in six places. Defaults stay pointed at the real repos —
that is on-message for a lab whose subject *is* `ml-python-base` — but they become
configurable.

Add to `Settings` ([config.py](../../src/harness_lab_app/config.py), prefix `HARNESS_LAB_`):

```python
template_repository: str = "marcosdh1987/ml-python-base"
lab_repository: str = "marcosdh1987/ai-agentic-harness-lab"
guide_repository: str = "marcosdh1987/harness-engineering-guide"
```

Call sites to migrate:

- [api.py:1139, 1155, 1221](../../src/harness_lab_app/api.py#L1139)
- [harness_lifecycle/cli.py:42-43](../../src/harness_lab_app/harness_lifecycle/cli.py#L42) (`LAB_REPOSITORY`, `TEMPLATE_REPOSITORY`)
- [models.py:316](../../src/harness_lab_app/models.py#L316) (`repository` field default)
- `Makefile:26` (`TEMPLATE_REPO`) — already `?=`, only needs documenting
- [scripts/lab_improve.py:45-47](../../scripts/lab_improve.py#L45) — already env-driven; align the variable names with the new settings

`models.py:316` is a **persisted column default**. Changing it does not rewrite existing
rows, which is correct — historical proposals keep the repo they were authored against.

### 1.2 LAN addresses → one shared placeholder

`192.168.0.223` (LM Studio) and `192.168.0.155` (Ollama) appear in eight files as
examples. Concrete examples are good UX, so keep the *shape* and drop the personal
address. Define once and import, so they cannot drift:

```python
# harness_lab_app/constants.py
EXAMPLE_LMSTUDIO_URL = "http://192.168.1.50:1234/v1"
EXAMPLE_OLLAMA_URL = "http://192.168.1.50:11434/v1"
```

Call sites: [api.py:420, 464](../../src/harness_lab_app/api.py#L420),
[lmstudio_direct.py:205](../../src/harness_lab_app/lmstudio_direct.py#L205),
[ollama_direct.py:169](../../src/harness_lab_app/ollama_direct.py#L169),
[run_service.py:305, 388](../../src/harness_lab_app/run_service.py#L305),
[settings_service.py:139](../../src/harness_lab_app/settings_service.py#L139),
[Settings.tsx:390, 467](../../frontend/src/pages/Settings.tsx#L390),
[docs/local-model-runtime-config.md:194, 241](../local-model-runtime-config.md).

The frontend needs its own copy in `frontend/src/constants.ts` — do not add an endpoint
just to serve two strings.

**Exit:** `git grep -nE "192\.168\.0\.(223|155)"` returns nothing — the personal LAN
addresses are gone entirely.

`marcosdh1987` deliberately *stays*, and stays widespread (README, onboarding, the
settings defaults, and the fixtures that assert against them). It is the real owner of
the repositories this lab measures, so it is the correct default rather than a leak;
Phase 1 makes it configurable, not absent. Grepping for it is therefore not an exit
criterion — an earlier draft of this plan claimed it returned only `Makefile` and
settings defaults, which was never true.

## Phase 2 — Publishing hygiene

- **`LICENSE`** — Apache-2.0 full text; `pyproject.toml` gets `license = "Apache-2.0"`
  and a `classifiers` entry.
- **`pyproject.toml`** — `name = "agentic-harness-lab"`; replace the Spanish
  `description = "Template base para proyectos de ML"` with an English one. Keep
  `[tool.hatch.build.targets.wheel] packages` unchanged (both packages still ship).
- **`CONTRIBUTING.md`** — the working loop already documented in
  [CLAUDE.md](../../CLAUDE.md) and [docs/agentic-workflow.md](../agentic-workflow.md),
  plus `make check` / `make check-sync` as the gate, and the "never commit generated
  artifacts by hand" rule.
- **`SECURITY.md`** — reporting contact; note that runs execute untrusted model output
  inside Docker and that `Dockerfile.sandbox` is the isolation boundary.
- ~~`skills-lock.json` timestamp churn~~ — **dropped: misdiagnosed.**
  [`render_lock`](../../src/ml_python_base/skills_sync/lockfile.py#L61) already
  preserves `generatedAt` unless the external skill set changed, and repeated syncs
  leave the file byte-identical. The two bumps observed while triaging were correct
  behaviour: a duplicate external skill really had appeared, then been removed.

### CI gaps

The existing workflow already runs `make check` and `make check-sync` — its name
("Docs and Quality Guardrails") undersells it. Two real gaps:

1. **No frontend gate.** `make check` covers ruff, bandit, mypy, pytest over
   `src/ tests/ scripts/` only. Phase 4 is a TypeScript change with no CI coverage. Add
   `tsc --noEmit` + `vite build` as a `make check-frontend` target and a CI step.
2. **PR-only trigger.** Add `push` on the default branch so main is continuously green.

## Phase 3 — Batch runs (backend)

### Contract

```
POST /api/runs/batch
{
  "case_ids":        ["tetris-v1", "cli-textstats-v1"],
  "harnesses":       ["opencode", "claude"],
  "prompt_variants": ["naive", "sdlc"],
  "executor_models": ["gateway/qwen3.6-35b"],
  "planner_models":  [null],
  "repetitions":     3,
  "config":          { ...shared RunConfigCreate fields: max_steps, skills_disabled, ... }
}
→ 201 { "batch_id": "...", "run_ids": [...], "count": 24 }
```

### Design constraints, and why

- **Validate everything before creating anything.** `create_run` today is
  preflight → id → config → run → publish ([run_service.py:493](../../src/harness_lab_app/run_service.py#L493)).
  Looping it naively yields half-created batches when combo 17 of 24 fails preflight.
  Validate the full cross-product first, return `422` naming the offending combination,
  then create.
- **Deduplicate preflight.** `preflight` makes **network calls** — gateway probe and
  context-window check ([run_service.py:210-230](../../src/harness_lab_app/run_service.py#L210)).
  These depend only on `(harness, executor_model, planner_model)`, not on case or
  prompt. A 24-run batch must issue ~2 probes, not 24. Cache per-request by that key.
- **Cap the size.** `HARNESS_LAB_MAX_BATCH_RUNS` (default 50), rejected with `422`. A
  four-list cross-product reaches hundreds of Docker runs from one careless click.
- **`batch_id` on `Run`.** Nullable column + `db.py` migration alongside the existing
  `skills_disabled` / `skills_extra` backfill ([db.py:77](../../src/harness_lab_app/db.py#L77)).
  Add `batch_id` as a filter on `GET /api/runs`.

Run ids come free: `_next_run_id(case_id)` already allocates sequentially per case.

### Tests

`tests/harness_lab_app/test_run_batch.py` — cross-product cardinality; preflight called
once per unique model/harness key; nothing persisted when one combo fails; cap
enforcement; `batch_id` groups and filters correctly.

## Phase 4 — Batch runs (frontend)

[Runs.tsx](../../frontend/src/pages/Runs.tsx) currently builds one `RunConfigCreate`.
Changes:

- Case picker → multi-select; harness (`HARNESS_OPTIONS`,
  [Runs.tsx:30](../../frontend/src/pages/Runs.tsx#L30)), prompt variant and executor
  model → multi-select. The `Set<string>` pattern already used for `disabledSkills` /
  `extraSkills` carries over.
- A **repetitions** field.
- A live "**this will queue N runs**" counter next to the submit button, disabled past
  the cap. Non-negotiable: the cost of a mis-click here is hours of GPU time.
- Runs table: `batch_id` column plus a filter.

Keep the single-run path intact — one selection per list must behave exactly as today,
including `POST /api/runs` staying the endpoint for a genuine single run.

All UI copy in **English** — the repo has drifted here before.

## Phase 5 — Documentation

- `README.md` — lead with the `ml-python-base` framing and state the coupling plainly,
  so the limitation is discovered in the README rather than after a clone.
- `docs/guide-web-ui.md` — batch flow.
- `docs/onboarding.md` — the new settings from Phase 1 (per `memory/` convention: new
  setup surface is documented in the same change).
- `docs/adr/0023-batch-run-execution.md` — record all-or-nothing validation, preflight
  deduplication, and the batch cap.

## Explicitly out of scope

- **Harness as a data input** (`repo_root` → per-run `harness_root`, materializing a
  harness by git ref into `workspace/<run_id>/.harness/`). The prerequisite for testing
  harnesses the lab does not live inside, and the real fix for the class of bug that
  dirtied the skills manifest — a benchmark run mutating tracked governance files
  in-place via [build_effective_skills()](../../scripts/bench_run.py#L294).
- **MCP in the harness builder** — `.mcp.json` is static and not per-run selectable.
- **Suite runner in the UI** — [harness-regression-v1/suite.yaml](../../benchmarks/harness-regression-v1/suite.yaml)
  already models cases × repetitions × gates. Layer it over `/api/runs/batch` once that
  exists.
- **Hermes and other harness adapters** — cheap per adapter, but only meaningful after
  the harness is an input.

## Sequencing

Phases 1, 2 and 3 are independent and can run in parallel. Phase 4 depends on 3;
Phase 5 depends on all. Phases 1 + 2 alone make the repository publishable.

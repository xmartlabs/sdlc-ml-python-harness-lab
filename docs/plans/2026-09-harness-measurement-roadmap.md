# From "the lab can run bare" to "the lab can say how much the harness helps"

> **Status (2026-09-08):** Phases 0, 1, 2 and 3.1 are implemented — see ADR-0050
> (a case ships its own tests), ADR-0051 (the ladder as one family) and ADR-0052
> (the installed manifest as the version axis). Deferred, none of which blocks
> running or reading an experiment: the judge N=3 sampling and `judge_unstable`
> (2.4), the headroom endpoint (1.4), the `Ladder` view, the proposal bridge and
> proposal→staged-skill draft (3.2-3.3), the activation template changes (Phase 4),
> and the critique pass for audited repositories (Phase 5).

## Context

The first genuinely bare-vs-full-harness experiment on Tetris (`…-on-th`, Haiku 4.5,
2 reps) came back **invalid**, and every reason it did is a defect of the *instrument*,
not of the harness:

| what the experiment showed | why the lab could not read it |
|---|---|
| treatment invoked 0/25 skills although the preamble names three as mandatory | the activation block was off; nothing measures compliance with preamble *prose* |
| the bare arm passed 2/2 | the corpus is one case (tetris-v1), binary, and Haiku solves it bare |
| equivalent implementations scored **10 and 2** by the judge on identical evidence | the rubric's levels 3-10 describe runtime gameplay no diff or headless smoke can establish; one sample, no reliability signal; the score never reaches the verdict anyway (not in the score registry) |
| harness cost **+71 %** but the "Total tokens" highlight showed it *lower* | `collect_claude_usage` ignores `cache_read/creation_input_tokens` (724k vs 184k) and highlights carry no direction |

And underneath: the case matrix keeps only the *last* repetition per case
(`experiment_service.py:515`), `.harness-version.yaml` does not exist so every run's
`template_ref` is empty (the leaderboard's harness axis is a content hash), the two
proposal pipelines never met, and an audit of an external repo can only *describe*.

**End state:** (A) a valid, repeatable answer to "does the harness help, and by how
much" — as a *ladder* (nothing → governance → +skills → +harness prompt → +activation),
per model tier, on a corpus with headroom; (B) the same machinery pointed at an audited
repository's own harness, with proposals that route to *that* repo; (C) the lab's own
harness improved through a closed, versioned loop where every change is a release that
is measured before it is adopted.

**Design rules carried through every phase** (from ADR-0034/0038/0046/0048/0049):
a metric never changes meaning under a stable name; an opinion never becomes the success
metric; a validity issue is stated before the verdict; nothing is auto-adopted.

---

## Phase 0 — Fix the instruments (week 1)

Everything here is small and blocks reading any experiment honestly.

**0.1 Tokens and highlights.** Do **not** redefine `total_tokens` (ADR-0034: a name keeps
its meaning; `experiment_service` pools by `score.name`, not by version).
- `scripts/bench_run.py:collect_claude_usage` (1916-1924): also sum
  `cache_read_input_tokens` / `cache_creation_input_tokens`; `write_usage_json` emits
  `cache_read_tokens`, `cache_creation_tokens`.
- `src/harness_lab_app/validation.py:collect_cost` (105-121): keep `total_tokens`; add
  `cache_read_tokens`, `cache_creation_tokens`, `billed_tokens` (all four summed, missing
  = 0 so the pinned fixtures in `test_validation.py:84`, `test_validation_service.py:106`,
  `test_evaluation.py:161` still hold).
- `scores.py`: three EFFICIENCY definitions (`billed_tokens` LOWER_IS_BETTER);
  `evaluation.EfficiencyEvaluator` emits them; fix `total_tokens` description.
- `statistics.py:294` `HEADLINE_METRICS = ("cost_usd", "billed_tokens", "agent_steps",
  "wall_time_minutes")`; `_highlight` adds `direction` from `REGISTRY[name].direction`
  (success → higher_is_better). `frontend/src/pages/Experiments.tsx:280-309` renders ▲/▼
  with better/worse colouring. Tests: `test_statistics.py`, `test_experiments.py` summary
  shape.

**0.2 Case matrix across repetitions.** `experiment_service.py:515` overwrites
`verdicts[label][case_id]` per run → the fixed/broken/unchanged matrix reflects the last
repetition only. Aggregate (all-pass / any-pass / rate, choose all-pass for `fixed`, and
expose the rate). The ladder (Phase 2) reads this. Test: `test_experiments.py` case_matrix.

**0.3 Judge: evidence-typed rubric, then registry, then sampling — in that order.**
Sampling an ungradeable rubric three times yields a stable wrong number.
- Convention, no schema: each rubric level line carries `[check]`, `[diff]` or `[manual]`.
  `harness_lifecycle/judge.build_rubric_message` parses the tags;
  `RUBRIC_SYSTEM_PROMPT` (judge.py:43-64) is rewritten so `[manual]` levels are never
  awarded, `[diff]` levels require a quoted code citation in the JSON (`"evidence"`
  field), `[check]` levels follow the PASS/FAIL/INCONCLUSIVE evidence. Retag
  `benchmarks/tetris/scoring.md` — which honestly caps tetris-v1 at level 2 and is the
  argument for tetris-v2 (Phase 1).
- `rubric_score` into the registry: category OUTCOME, provenance JUDGEMENT,
  `source="llm_judge"`, 0-10, emitted only when `subjective.score_source == "llm_judge"`
  (`score_service.build_context` passes `validation.subjective`). Never the success
  metric; not in `EXPORTED_SCORES` (`test_score_export.py:100`). It becomes visible in
  the deltas table, which is where the 10-vs-2 would have been caught.
- Later (Phase 2, step 2.4): N=3 samples via a loop in `_worker_gateway`, median +
  `rubric_score_spread` (OBSERVATION), validity code `judge_unstable` — **DEGRADED, never
  INVALID** (the causal verdict rides `task_validation_pass`).
- Skip a separate `JUDGE_MODEL`: `settings_service.py:144-164` argues for one judge env.

**0.4 Attribution bias for in-target runs.** `attribution._normalize_event_path`
(134-136) strips `/app/` blindly, so a native run's read of *its own* rulebook never
matches its surface while a read of the lab's does. Normalise against the run's harness
root. Tests: `test_attribution.py`, `test_governance_surface.py`.

**0.5 ADR-0049 erratum.** ADRs are immutable (`docs/adr/README.md:4`): add an
"Erratum" section — `apiKeySource: "none"` is how OAuth auth *always* reports, not
evidence of a gateway bypass (the lab_root arm shows the same). Fix the mirrored comment
in `executors.py:283`, `docs/harness-adapters.md:92`, `memory/learnings.md:866`,
`test_executors.py:423`. The env injection stays: routing identical across arms was the goal.

**0.6 Hygiene.** `make case-import TASK=benchmarks/cli-textstats/task.yaml`;
`benchmarks/harness-regression-v1/suite.yaml` names only cases that exist;
`benchmarks/README.md` stops listing cases that do not.

**0.7 Re-ask the invalid question correctly — this week, ~$5.** "Prose vs activation
block": two `lab_root` arms, `harness` prompt on both, `skills_invoke=[]` vs the catalog,
5 reps, Haiku, tetris-v1 + cli-textstats. Needs nothing new (`POST /api/experiments`).
It answers *why* the preamble did not activate anything before any harness edit is made.

---

## Phase 1 — A corpus with headroom (week 2)

**1.1 Graded success without a schema change.** Do **not** make `success_metric`
selectable: `summarize_condition` casts `bool(scores[metric])`, so `test_pass_rate=0.3`
would count as success, and `_ceiling_issue`/`paired_by_case` would need parallel
thresholds. Instead: `task_validation_pass` stays the strict boolean ("every shipped
assertion passed"), `test_pass_rate` goes **first** in `HEADLINE_METRICS` and
`_ceiling_issue` (`statistics.py:517-554`) also fires `no_headroom` when the control's
`test_pass_rate` mean is 1.0.

**1.2 A case ships its own tests — `tetris-v2`** (ADR-0050 "a case ships its own tests").
- `benchmarks/tetris-v2/prompt_default.md` states a `Game` API contract (`Game()`,
  `.board`, `.step()`, `.move(dx)`, `.rotate()`, `.score`, `.game_over`) plus the
  `BENCH_SMOKE` contract; `tests/test_game.py` with ~10 headless tests
  (`SDL_VIDEODRIVER=dummy`); one validation command **per test** (so `test_pass_rate`
  has resolution 10 with zero parser work — a junit strategy in `parse_objective` is the
  later refinement); tamper guard first: `git -C /app diff --quiet HEAD --
  benchmarks/tetris-v2` and `files_forbidden: ["benchmarks/**"]`.
- Runner image: pytest is dev-only (`Dockerfile.runner:52 uv sync --no-dev`); add it at
  line 58 beside pygame. Validation runs unmasked from `/app`, so `/app/benchmarks/…` is
  reachable on every arm.
- Scoring rubric tagged `[check]`/`[diff]`; `make prompts`; `make case-import`.
- Tell the agent the tests exist (both arms discover the same contract).

**1.3 Two more graded cases** via `case_draft_service` (`POST /api/cases/generate-draft`,
already demands functional assertions): `bugfix-with-tests` (red test → fix, tests
shipped as in 1.2) and one API/CLI case. One or two SWE-bench Lite flask instances
(`_ENV_SPECS` supports only `pallets/flask`) — lowest priority: binary, and the
`swe_default` cwd hygiene leak (Phase 4) makes its bare arm not bare.

**1.4 Headroom before, not after.** `make calibrate CASE= MODEL= REPS=5` posts a bare
batch through the existing `POST /api/runs/batch`; `GET /api/cases/{id}/headroom?
executor_model=` aggregates `task_validation_pass`/`test_pass_rate` of bare runs for that
case+model; the wizard warns at case selection when bare pass rate is 1.0. No new
column, no new batch machinery.

---

## Phase 2 — The harness ladder (week 3)

Five rungs, each a single-factor `harness_evaluation`, model fixed, 5 reps:

| rung | control → treatment | factor (wizard) | state |
|---|---|---|---|
| 1 | bare → governance-only (skills hidden, `default` prompt) | Governance body | exists |
| 2 | governance-only → +skills available | Remove skills (inverted) | exists — **will raise `treatment_not_activated` by construction; that is the finding, the view must say so** |
| 3 | +skills → +`harness` prompt | **Harness prompt** (prompt_variant only) | new factor |
| 4 | +harness prompt → +activation block | **Activation** (skills_invoke ∅ vs catalog) | new factor |

Rungs 3-4 sit on `lab_root` (a bare arm cannot be their control — `_BARE_SUBSUMED`).

- `Experiment.family_id` (copy the `suite_id` pattern: `models.py:331`, `db.py:178`,
  `schemas.py:164`, migration test). `scripts/ladder.py` creates the family through
  `ExperimentService.create` (modelled on `suite_experiment.experiment_from_suite`);
  `make ladder SUITE= MODEL= REPS=`. Family filter on `Experiments.tsx` first; a Ladder
  view (cumulative deltas + validity per rung) once rung data exists. ADR-0051
  "experiment families: the harness ladder".
- Harness value is a function of (harness CLI, model): run the ladder per pair — Haiku
  under `claude`, a local Qwen under `opencode` — and never compare rungs across pairs.
- Budget: 4 rungs × 2 arms × 5 reps × 2 cases × ~$0.25 ≈ **$20, ~2.5 h** on Haiku.
- 2.4 Judge N=3 + `rubric_score_spread` + `judge_unstable` (from 0.3), now that a case
  has diff-evident levels.

**Deliverable of Phase 2 = the first answer to (A):** per rung, the delta in
`task_validation_pass`, `test_pass_rate`, `cost_usd`, `billed_tokens`, with validity.

---

## Phase 3 — Close the lab's own loop (week 4) — *before* touching the harness

Harness artefacts are template-synced; editing them locally is drift
(`make harness-status`). So the loop must exist before the activation fixes.

- **3.1 `.harness-version.yaml`** — the single highest-leverage missing file.
  `harness-adopt REF=` subcommand in `harness_lifecycle/cli.py` resolves the commit
  (`releases.resolve_ref_commit`) and writes the `InstalledManifest`; `template_ref`
  becomes real on every run; leaderboard and `overview_service` trend by release. ~½ day.
- **3.2 One bridge, not a unification**: `SavedProposal → Proposal`;
  `harness-proposal --from-saved ID`. Everything new goes through the typed model.
- **3.3 Proposal(kind=skill) → staged skill draft + experiment draft** (`skills_extra`),
  mirroring `finding_service.experiment_draft`. A draft, never auto-created (ADR-0035).
- **3.4 Adopt/rollback v1**: sync-branch → PR to `ml-python-base` → release tag →
  `harness-adopt` → suite re-run → compare two experiments in one family by
  `template_ref`. Rollback = adopt the previous ref. ADR-0052 "the installed manifest is
  the version axis". A true `harness_ref` *condition axis* (candidate worktree mounted as
  a run's harness) is out of reach without a mount change in `bench_run`/`executors` —
  last item of the roadmap, its own ADR.

---

## Phase 4 — Activation, measured (week 5)

Informed by 0.7 and rung 4. Template PRs, each a release measured via 3.4:
skill descriptions retargeted to task types (ADR-0041 taxonomy: today all three preamble
skills key on the lab's `make check` loop, not on "build a game"); hooks that name the
`Skill` tool rather than slash commands; the SWE preamble (`case_service.py:22-45`, two
skills) and `benchmarks/_harness_preamble.md` (three) aligned on the skill list. A
preamble change is a release compared across time, not a third prompt variant
(ADR-0047 pins exactly two).

---

## Phase 5 — Critique audited repositories (week 6+)

Keep "describí, no prescribas" in `bench_audit.py` — the descriptive pass feeds
attribution and `harness_audit` scores and must stay opinion-free. Add a **separate
critique pass** with its own prompt that consumes the descriptive audit, readiness checks
and the target's rules file, and is allowed to prescribe **for the target only**.
- `lab_improve.route_repo` third branch on `repo_hint == "target"`;
  `SavedProposal.repo = "target:<id>"` (free string, no migration); typed
  `Proposal.destination` + `schema_version` bump.
- Auto-drafted `Finding(origin="critique", target_repo_id=…)` — level stays human-scored.
- `audit_report._next_steps` built from critique proposals; the 3-branch table is the fallback.
- Native-vs-injected preset = the target's ladder floor (bare is not available with a
  target; `native` is). Fix the `swe_default` cwd hygiene leak here.
- ADR-0053 "the audit describes; the critique prescribes for the target".

---

## Do first (this week, in order)

1. 0.1 tokens/highlights + 0.2 case matrix — `make check` green.
2. 0.3 rubric tags + `rubric_score` in the registry + rewritten judge prompt.
3. 0.6 import cli-textstats, fix suite.yaml/README.
4. 0.7 launch "prose vs activation block" (5 reps, Haiku, ~$5).
5. 3.1 `harness-adopt` + `.harness-version.yaml` (half a day; makes every run from now
   on attributable to a release).
6. 0.4, 0.5.

## Verification

- Per step: `make check`, `make check-frontend`; new tests named in each item.
- Phase 0 done when: re-summarising `…-on-th` shows `billed_tokens` treatment > control
  with ▲/▼, a `rubric_score` row per run with the 10/2 split visible in the deltas, and a
  corrected case matrix.
- Phase 1 done when: `GET /api/cases/tetris-v2/headroom?executor_model=claude-haiku-4-5`
  reports bare `test_pass_rate` < 1.0 over 5 runs.
- Phase 2 done when: `make ladder` produces a family of four valid (or honestly
  degraded) experiments and the rung table reads without a caveat that contradicts it.
- Phase 3 done when: a run's `metadata.json.template_ref` is a `vX.Y.Z` tag and the
  overview trend groups by it.

No commits, no staging: report changed files and a suggested commit message as text.

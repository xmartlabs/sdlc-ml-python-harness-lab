# Roadmap: Lab v2 — from run executor to evaluation instrument

**Status:** implemented (2026-08-30) — all twelve phases landed. Decision records:
[0028](adr/0028-evaluation-modes.md) modes ·
[0029](adr/0029-target-repositories-pinned-per-run.md) target repositories ·
[0030](adr/0030-one-workspace-source-interface.md) workspace sources ·
[0031](adr/0031-harness-source-is-a-run-condition.md) harness conditions ·
[0032](adr/0032-cases-are-derived-from-a-targets-own-history.md) case derivation ·
[0033](adr/0033-experiment-above-run.md) experiments ·
[0034](adr/0034-score-registry.md) score registry ·
[0035](adr/0035-findings-and-manual-maturity-assessment.md) findings and assessment.
Guide: [guide-target-repositories.md](guide-target-repositories.md).

The phases below keep their original text; each carries a **Met** note recording
what actually shipped and what was deliberately left short.
**Supersedes in scope:** nothing. [roadmap-local-webapp.md](roadmap-local-webapp.md)
described building the lab; this describes turning it into an instrument.

## Purpose

v1 answers *"what happened in this run?"*. v2 must answer:

> **How does a harness behave across repositories, cases and configurations —
> with evidence comparable enough to defend a conclusion?**

That is a different product. A run executor optimises for "start the container and
show me the log". An evaluation instrument optimises for **valid comparison**: a
stated question, a control, a treatment, repetitions, dimensions that are not
silently averaged together, and an explicit record of what the evidence does *not*
support.

The roadmap is ordered so that no phase has to be redone by a later one. The
sequence is deliberate: **domain first, execution second, evaluation third, UI
last.** Redesigning screens before `Experiment` exists produces screens that model
the wrong nouns.

## What already exists

v2 is mostly *promotion of existing machinery into first-class concepts*, not
greenfield. The honest starting position:

| v2 concept | What v1 already has | Gap |
|---|---|---|
| Experiment | `POST /api/runs/batch` — cross-product of cases × variants × harnesses × models × repetitions, capped and preflighted ([ADR-0023](adr/0023-batch-run-execution.md)); `Run.batch_id` as a grouping label | No question, no control/treatment labelling, no aggregation, no experiment entity |
| Condition (arm) | `provenance.condition_hash` + `harness_fingerprint` ([ADR-0003](adr/0003-trial-provenance-and-harness-fingerprint.md)); skill ablation folded into the hash ([ADR-0011](adr/0011-per-run-skill-ablation-and-staging.md)) | Conditions are *derived* from a run, never declared up front. [Compare.tsx](../frontend/src/pages/Compare.tsx) recovers case/prompt/model by parsing the runner **command string** |
| Target repository | `Case.subject_repo` + `Case.base_commit`; `provision_workspace` in [bench_run.py:1060](../scripts/bench_run.py#L1060) clones at a commit, applies `test_patch`, commits, installs | Bound to the *case*, not to a reusable repository entity. No ref→SHA resolution for a moving ref like `main`; no per-repo setup/validation contract |
| Workspace materialization | Per-run `workspace/<run_id>` with `validate/provision/finalize`; SWE-bench Pro pre-provisioned worker-side; continuation seeding ([ADR-0026](adr/0026-run-continuation-as-child-runs.md)) | Three lanes (tetris / repo-task / pro) branch inside one script instead of implementing one interface |
| Deterministic outcome | `Validation` — objective / cost / effort / subjective / composite ([ADR-0004](adr/0004-objective-validation-and-multidimensional-score.md)); SWE lanes auto-score binary from F2P/P2P | Dimensions are free-form JSON. No named, versioned metric registry; no forbidden-files or regression signals |
| Behavioural evaluation | `Audit` — markdown + `findings_json` per-item verdicts; `Attribution` — tools, files, governance-doc read coverage ([ADR-0005](adr/0005-structured-harness-attribution.md)) | Verdicts are categorical prose, not scores on named axes; facts, observations and judgements are stored and rendered alike |
| Harness injection | `skills_sync` is **root-parameterized** end to end — `discover_skills(root)`, `link_tool(root, …)`, `render_tool(root, …)`, `project_agents(root, …)` | Only [bench_run.py:353](../scripts/bench_run.py#L353) hardcodes `REPO_ROOT`. One root serves both *source* and *destination*; overlaying onto a foreign workspace needs them split |
| Audit subject | `bench_audit.py` reads run artifacts + governance rules | It reads `REPO_ROOT / rules_name` ([bench_audit.py:368](../scripts/bench_audit.py#L368)) — **the lab's own** rules, never the target's |
| Improvement proposals | `lab_improve.py` → `SavedProposal` → GitHub issues | `route_repo` only resolves to `TEMPLATE_REPO` / `HARNESS_REPO`, and the prompt states proposals target a doc/skill change *"not the subject task"* — it cannot address the client's repo |
| Telemetry | Langfuse traces rebuilt from run artifacts over OTLP ([ADR-0027](adr/0027-langfuse-traces-rebuilt-from-run-artifacts.md)) | Traces only — **no scores are ingested**, so Langfuse cannot yet filter or trend on lab outcomes |
| Aggregation | `LeaderboardService.list_rows` | One row per run. No mean/median/spread, no per-condition rollup, no delta |

The load-bearing limitation stays as documented in
[known-limitations.md](known-limitations.md): `repo_root` defaults to `Path.cwd()`,
so **the harness under test is the repository the lab runs from**. v2 changes the
*target* side of that equation and deliberately leaves the harness side alone
(see [Out of scope](#explicitly-out-of-scope-for-v2)).

## The three modes

The modes are the product's vocabulary, not documentation flavour. Each one fixes
a different set of factors, and therefore each one licenses a different class of
conclusion. Encoding them lets the UI **refuse to render a comparison that the
design does not support**.

### Mode A — Harness Evaluation

*Does this change to the harness cause a better outcome?*

| Held fixed | Varied |
|---|---|
| target repo, case, model, environment | skill, rule, prompt, subagent, workflow, harness version |

```text
Target repo: company/backend      Control:   ml-python-base v0.5
Case:        safe-database-migration
Model:       claude-opus-5        Treatment: v0.6 + migration-safety skill
```

The most rigorous mode, and the only one that supports a causal claim — and only
with ablation plus repetitions, which is exactly the method already argued in
[ADR-0003](adr/0003-trial-provenance-and-harness-fingerprint.md).

**The consulting shape of Mode A** is a harness condition pair on someone else's
repository:

```text
Target:  client/payments-api @ <resolved SHA>
Case:    add-endpoint-with-tests   (derived from their merged PRs)
Control:    native    — the repo as it is: their CLAUDE.md, their conventions
Treatment:  injected  — the same repo with ml-python-base materialized on top
```

This answers *"what does our harness add over how this team already works?"* on
their code, with their task, at fixed model and N repetitions. It is the hardest
claim to argue with — and the reason harness **source** becomes a property of a
condition (see [Domain model](#domain-model)).

### Mode B — Cross-Repo Evaluation

*Does the harness work outside the repository it was designed in?*

| Held fixed | Varied |
|---|---|
| harness, skills, model, task family | target repository |

Scores from different repositories **must not be compared as if conditions were
identical**. What this mode legitimately produces: where the harness holds, where
it degrades, which repository properties break the agent, which skills transfer.

### Mode C — Discovery / Transfer

*What can we learn from how this team works with agents?*

No causal claim at all. Output is a report, not a scoreboard: practices detected,
missing skills, redundant skills, conventions worth porting, candidate eval cases,
proposed experiments. Every artifact carries the label **exploratory evidence, not
experimental evidence**. That label is a methodological guard, not a disclaimer.

## Domain model

```text
Experiment
├── mode                  harness_evaluation | cross_repo | discovery
├── question              the sentence the experiment answers
├── conditions[]          one control + N treatments
│   ├── HarnessCondition  source (native | injected), fingerprint, version,
│   │                     skills enabled/disabled/staged
│   ├── ModelCondition    alias, temperature, context
│   └── Environment       runner image, sandbox fingerprint
├── targets[]             TargetRepository
├── cases[]               Case
├── repetitions           N
├── evaluation_profile    which evaluators run, which scores are collected
└── runs[]                Run  (the existing entity, unchanged in kind)
```

`Run` stays what it is. The change is that it stops being the top-level noun.

### TargetRepository

```yaml
id: company-payments
repository: git@github.com:company/payments-api.git
ref: main                     # what the user picked
setup:
  - uv sync
validation:
  - make lint
  - make test
metadata:
  language: python
  team: payments
```

Non-negotiable: **the resolved commit SHA is persisted per run**, even when the
user selected a moving ref. A run whose provenance says `main` is not reproducible.
This mirrors what the SWE lanes already do with `base_commit` — v2 generalises it
rather than inventing it.

### Case as a first-class entity

`Case` already carries `validation_commands`, `allowed_files`, `success_criteria`,
`scoring_rubric` and `tags`. v2 adds what an organizational benchmark needs:

```yaml
target_requirements:
  files_expected:  [...]
  files_forbidden: [...]      # new — today only allowed_files exists
validators:
  - pytest
  - migration_check
rubric:
  functional_weight:  0.60    # new — weights are prose today
  behavioral_weight:  0.20
  efficiency_weight:  0.20
```

Case provenance splits three ways: **public** (SWE-bench, curated benchmarks),
**synthetic** (handcrafted), **organization** (recurring tasks, incidents, failed
AI PRs, architecture violations, security findings). The third is what makes a
regression suite that outlives an engagement — and it is the only provenance that
makes a comparison on a client repository mean anything, which is why deriving it
from that repository's own history is a phase of its own (Phase 3).

## Evaluation model

Four levels. They are collected separately and **never collapsed silently**.

**Level 1 — Deterministic outcome.** The base of the score, and the only level
with ground truth when one exists.

```text
task_validation_pass      bool     test_pass_rate            0..1
lint_pass                 bool     build_pass                bool
security_pass             bool     expected_files_modified   0..1
forbidden_files_modified  count    regression_count          int
```

**Level 2 — Efficiency.** Objective wherever the artifacts allow it: wall time,
agent steps, tool calls, LLM calls, tokens in/out, cost, diff lines ±, files
changed, retries, failed tool calls. Then the derived ratios that actually drive
a business decision — `cost_per_success`, `tokens_per_success`,
`steps_per_success`. 92% success at $4.00/run can be a worse buy than 89% at
$0.70.

**Level 3 — Trajectory.** DeepEval as an **optional adapter** (Task Completion,
Step Efficiency, Tool Correctness first; Plan Quality and Plan Adherence later) —
never as the platform. The lab owns the evaluation model; DeepEval computes some
of the numbers in it.

**Level 4 — Harness behaviour.** The differentiated layer already built. The LLM
audit must emit structure alongside its prose:

```json
{"planning_quality": 0.84, "verification_quality": 0.92,
 "skill_adherence": 0.76, "sdlc_adherence": 0.88,
 "looping_detected": false, "unnecessary_actions": 2}
```

with `reason`, `evidence_refs` and `confidence` per axis.

### Facts, observations and judgements are different things

```text
test_pass_rate  = 1.0     fact        (a command exited 0)
skill_file_read = true    observation (parsed from the step log)
skill_usefulness= 0.81    judgement   (an LLM's opinion)
```

The API must carry the provenance and the UI must render the three differently.
Mixing them is how a lab loses the right to be believed.

### Score registry

Every score is a declared, versioned definition — not an ad-hoc JSON key:

```yaml
name: step_efficiency
category: trajectory
type: numeric
range: [0, 1]
direction: higher_is_better
source: deepeval
version: v1
```

The reason is concrete: when the judge or rubric changes in six months,
`step_efficiency_v1` must not silently pool with `step_efficiency_v2`.

### Composite scores

A composite may be shown; it may never hide its dimensions.

```text
Functional 92%   Behavioral 81%   Efficiency 74%   Cost $0.81
Composite  0.84  (formula disclosed)
```

Never an opaque "Harness Score = 87".

## Repetitions and statistics

Agents are stochastic and local models at temperature > 0 are reproducible only
*statistically* — the position already taken in `memory/context.md`. v2 makes it a
product default rather than advice:

| N | Status |
|---|---|
| 1 | exploratory — the UI must say so |
| ≥ 3 | exploratory comparison |
| ≥ 5 | useful comparison |
| ≥ 10 | stronger evidence |

Per condition: mean, median, std, min/max, success rate. Per comparison: Δ mean,
Δ success rate, confidence interval. Bootstrap CIs, Mann-Whitney and paired tests
come later — good repetitions and visible distributions first.

## Phases

Each phase is shippable and has an exit criterion. Phases 1-3 are the structural
core; nothing after them is safe to build first.

### Phase 0 — ADRs and domain model

No code. Formalise: the three modes; Harness vs Target vs Case; Experiment vs Run;
which comparisons are valid; **what may not be concluded** from each mode.

*Exit:* ADRs accepted for modes, the experiment/run split, target repositories, the
workspace-source interface, the harness-source contract (`native` vs `injected`,
and what a fingerprint must cover for the two to stay distinguishable), and the
score-registry contract.

### Phase 1 — TargetRepository

`TargetRepository` entity; URL + ref; ref→SHA resolution at run creation; git
workspace materialization behind a `WorkspaceSource.materialize(...)` interface
with `Local` / `Git` / `SweBench` / `SwePro` implementations; lifecycle cleanup;
repository provenance in `metadata.json`.

The local repo stays supported as `LocalWorkspaceSource` — the current workflow
must not break — but it goes through the same interface, so the lab does not end
up with four execution engines.

*Exit:* run an existing case against an external repository without modifying that
repository or the lab. **Met (2026-08-30)** — `TargetRepository`, ref→SHA pinning at
run creation, the `git_target` workspace source, the repository axis in batches, a
Repositories screen, and repository-owned validation gates. The `WorkspaceSource`
interface landed as the explicit `source` discriminator ADR-0030 specifies, with the
existing lanes still behind their `case_type` branches — migrating those is the
incremental half of that ADR.

### Phase 2 — Harness conditions on an external target

The comparison the consulting work needs is `native` vs `injected` **on the
client's own repository**. That makes the harness a property of a condition
rather than a property of the machine the lab runs on.

Work: split `skills_sync`'s single `root` into `source_root` (where the governed
skills, agents and adapter templates come from) and `target_root` (where the
agent-visible surfaces are materialized); teach `WorkspaceSource` to overlay a
harness after the target is cloned; define the collision policy when the target
already ships a `CLAUDE.md` / `AGENTS.md`.

Two traps, both load-bearing:

- **`harness_fingerprint` must hash the workspace's surfaces, not the lab's.**
  Today it hashes `skills-lock.json`, `.github/agents` and the lab's governance
  docs. Left alone, `native` and `injected` produce the *same* `condition_hash`
  and the arms stop being distinguishable — which destroys exactly the property
  that makes the comparison valid.
- **Injection is not always additive.** Replacing or merging a client's existing
  governance is a design decision with methodological consequences, and it must be
  recorded in the condition, not decided implicitly by copy order.

*Exit:* the same case runs on the same external repository under both `native` and
`injected`, producing two distinct `condition_hash` values and a diff attributable
to the harness alone. **Met (2026-08-30)** —
[ADR-0031](adr/0031-harness-source-is-a-run-condition.md): `RunConfig.harness_source`,
`skills_sync.projection.project_harness` (copy-only, ablation-aware, collision-
recording), projection before the baseline commit, the workspace fingerprint that
keeps the two arms distinct, and a harness axis in batches. The `source_root` /
`target_root` split landed as an optional `template_root` on the renderer and a
`strategy` override on the linker, leaving in-place sync byte-identical.

### Phase 3 — Case derivation from a target

Running Tetris against a payments API measures nothing. Mode B and Mode C only
become meaningful once cases reflect **what that team actually does**: add an
endpoint with tests, migrate a schema, fix a class of bug.

Work: derive candidate cases from the target's own history — merged PRs, recurring
task shapes, incidents, reverted AI PRs — into the `organization` case provenance,
with `files_expected` / `files_forbidden` and validators taken from the repo's own
gates rather than invented.

**Authoring bias is the risk to design against.** A case we write can quietly
favour the harness we are selling. Two cheap mitigations, both mandatory: derive
cases from *merged* work rather than from a description given in a meeting, and
freeze the case and its rubric **before** either condition runs.

*Exit:* a case derived from a client repository's history runs green on the
`native` condition and is accepted by that team as representative of their work.
**Met (2026-08-30)** — [ADR-0032](adr/0032-cases-are-derived-from-a-targets-own-history.md):
deterministic mining from a blobless mirror (`case_derivation.py`), `Case.provenance`
plus `files_expected` / `files_forbidden`, `POST /api/targets/{id}/derive-cases`, and a
candidates panel on the Repositories screen. Candidates are proposals — nothing is
persisted, and the team's acceptance stays a human step by design.

### Phase 4 — Experiment abstraction

Experiment model, mode, declared conditions, repetitions, run grouping, experiment
API. Batch becomes the *execution strategy* of an experiment rather than a parallel
path; `benchmarks/harness-regression-v1/suite.yaml` becomes a producer of
experiments, closing the gap noted in
[known-limitations.md](known-limitations.md#suiteyaml-produces-experiments-not-batches).

*Exit:* a Mode A experiment (control vs treatment, N reps) created, executed and
grouped end to end, with conditions declared before execution rather than parsed
back out of a command string. **Met (2026-08-30)** —
[ADR-0033](adr/0033-experiment-above-run.md): `Experiment` + `ExperimentCondition`,
`Run.experiment_id` / `condition_label`, and design validation that *refuses* a
comparison the declared mode cannot answer.

`suite.yaml` was the one part left short, and it closed the same day:
[ADR-0037](adr/0037-regression-suite-produces-experiments.md) makes a suite a
*producer* of experiments via `POST /api/experiments/from-suite`. The suite supplies
the cases and their per-case repetitions, the caller supplies the two arms, and the
suite's `controlled_fields` are enforced against that choice. There is no second
runner, so validating a harness release is read through the same case matrix as
every other comparison: **fixed / broken / unchanged**.

### Phase 5 — Evaluation schema

`ScoreDefinition`, `Score`, `Evaluator`, `EvaluationProfile`. First evaluators:
`DeterministicEvaluator` (Level 1), `HarnessBehaviorEvaluator` (the existing audit,
emitting scores), `EfficiencyExtractor` (Level 2 from `usage.json` and step logs).
No DeepEval yet.

**The audit must judge the right governance.** `bench_audit.py` reads
`REPO_ROOT / rules_name` — the lab's rules. On an external target that is simply
the wrong document, and under the `native` condition it is doubly wrong: the rules
under evaluation are *theirs*. The rules input becomes an explicit parameter
resolved from the workspace.

*Exit:* every completed run produces named, versioned scores with declared
provenance (fact / observation / judgement), and an audit of an external target
quotes that target's governance. **Met (2026-08-30)**, and completed the same day by
[ADR-0036](adr/0036-audit-judges-a-run-against-its-own-governance.md), which resolves
the governance surface per run from the workspace that run actually had and persists
its source, file list and fingerprint with the audit — so a `native` control is never
scored against rules it did not receive. Original delivery —
[ADR-0034](adr/0034-score-registry.md): a code-owned registry of 17 definitions,
three evaluators behind named profiles, `Score` rows keyed `name@version`, and
`bench_audit` now resolving its rules from the run's workspace for a `native` or
`injected` run. Scoring is on demand (`POST /api/runs/{id}/scores`) rather than
automatic after every run.

### Phase 6 — Langfuse scores

Ingest the important scores onto the traces that already exist: `test_pass_rate`,
`task_completion`, `step_efficiency`, `skill_adherence`, `composite_score`.

Division of ownership, permanently: **the lab DB is the source of truth for the
experiment domain; Langfuse is the evidence store for traces and telemetry.**
Langfuse has no concept of harness version, skill ablation, target repo, git ref or
sandbox fingerprint, and must not be asked to acquire one.

*Exit:* filter, compare and trend lab scores in Langfuse, with every trace
resolvable back to its run. **Met (2026-08-30)** — a curated subset is pushed as
`score-create` events onto each run's existing session, with the version folded into
the score name (Langfuse has no version field) and provenance carried in metadata.
One-way, as decided.

### Phase 7 — DeepEval adapter

Optional dependency. Task Completion, Step Efficiency, Tool Correctness. Evaluate
Plan Quality and Plan Adherence only after the first three prove stable.

*Exit:* trajectory scores appear for an experiment with DeepEval enabled, and the
lab behaves identically with it disabled. **Met (2026-08-30)** — the three trajectory
metrics are wired end to end: a run's recorded trajectory is mapped onto DeepEval's
test-case shape, and each score is persisted with its evaluator, adapter version and
the rubric or judge model behind it. Two of the three are `GEval` rubrics rather than
DeepEval's own metrics (its task-completion metric reads a *live* trace; the lab
evaluates archived runs, and step efficiency has no DeepEval metric at all) — both
are declared `judgement` in the registry, which is what they are. A metric that
cannot be computed — no DeepEval, no judge model, no trajectory, no declared
expectation — returns **no score**, never a zero. Plan Quality and Plan Adherence
stay out, as planned.

### Phase 8 — Statistical aggregation

Per condition: mean, median, std, success rate, CI. Per experiment: delta and
paired per-case comparison. Explicit low-N warnings.

*Exit:* an experiment reports a defensible delta with its spread, and a
single-repetition experiment is labelled exploratory wherever it is displayed.
**Met (2026-08-30)** — `statistics.py` with per-condition aggregates, deltas guarded
against being read inside the noise, a per-case matrix, and normal-approximation
intervals that return nothing below N=3. No significance tests, as decided.

### Phase 9 — UI information architecture

Only now. Navigation: Overview, Experiments, Runs, Cases, Repositories, Harness,
Skills, Evaluations, Insights, Settings.

- **Overview** — harness version, experiments this month, success rate, regression
  rate, median cost, cases in the regression suite, quality over time. Not
  operational detail.
- **Experiment detail** — the flagship screen. Header states mode and design
  (`12 cases × 5 repetitions`); a condition table with outcome / behaviour /
  efficiency / cost kept in separate panels rather than one merged chart.
- **Case matrix** — per-case ✅/❌ per condition. Far more useful than an average:
  it shows *where* a treatment helps and where it does nothing.

*Exit:* an experiment can be read and interpreted without opening a single run.
**Met (2026-08-30), and reworked on the same date** around *what the user came to
learn* rather than around the lab's own entities: a Home page whose first question is
"what do you want to learn?" and whose three answers are the three modes, an
attention list of only actionable signals, a wizard that cannot express a design the
lab would refuse, a six-step guided audit for Mode C, an experiment verdict that
answers the question in ten seconds without ever claiming statistical significance,
and navigation regrouped as Evaluate / Evidence / Advanced / System with every old
path redirecting. Original delivery: an Experiments screen (condition table, per-metric deltas
coloured by *declared direction*, the case matrix), a Findings screen, a Scores panel
that draws fact, observation and judgement differently, and an Overview that leads
with harness version, success rate, regressions, median cost and cost-per-success,
regression-suite size and quality over time — every figure with its denominator, and
computed server-side (`GET /api/overview`) so the definitions are testable rather
than implied by a chart.

### Phase 10 — Discovery mode

`Finding` (evidence, affected cases, severity, recommendation, candidate
intervention, status) and a discovery report that generates candidate skills, cases
and experiments — with a "create experiment" action from a recommendation. This is
the consulting chain, made mechanical:

```text
Audit → Finding → Hypothesis → Experiment → Evidence → Recommendation
```

**A finding must be able to point at the client's repository.** `lab_improve.py`
routes every proposal to `TEMPLATE_REPO` or `HARNESS_REPO`, and instructs the model
that proposals target a doc or skill change *"not the subject task"*. The whole
proposal → issue machinery is reusable; what it lacks is a third destination. A
finding about the *target* ("no lint gate, so the agent never self-corrects") is
the deliverable of an audit, and today it has nowhere to go.

*Exit:* a discovery report on an external repository yields at least one candidate
experiment created directly from a finding, and at least one recommendation
addressed to the target repository rather than to the lab's harness.
**Met (2026-08-30)** — [ADR-0035](adr/0035-findings-and-manual-maturity-assessment.md):
`Finding` with evidence, recommendation, intervention and the experiment that settled
it; `refuted` kept as a first-class outcome. The draft endpoint returns a design to
review rather than creating one.

Extended the same day with **suggested findings**: the corpus is mined for patterns —
regressions, cases that have never passed, harness items the auditor keeps judging
unused — and they reach a review queue labelled `suggested`. Accepting one requires a
name and produces a finding of record; rejecting one is written down so the pattern
is not re-raised. Nothing auto-promotes, which keeps "the lab noticed something" and
"the lab claims something" distinct.

### Phase 11 — Organizational assessment

A rubric over Standardization, Governance, Observability, Evaluation, Isolation,
Regression, Continuous improvement — scored **manually, supported by lab
evidence**. Not automated at this stage.

*Exit:* an assessment report whose every claim links to runs or experiments.
**Met (2026-08-30)** — a seven-dimension rubric with five observable levels each,
scored manually, where **a level above 2 without evidence is refused** and an unscored
dimension reports as unknown rather than zero.

## Explicitly out of scope for v2

Stated so the scope cannot drift by accident:

- **A harness sourced from a third repository.** The narrowing is on *where a
  harness comes from*, not on whether a foreign harness can be evaluated. Two
  sources are in scope: the lab's own (`ml-python-base`, injected — Phase 2) and
  **the one already living inside the target** (`native`). What stays out is
  `target repo A + harness repo B + plugin repo C`, which multiplies the state
  space before the evaluation model is proven. An `ExternalHarnessSource` comes
  only when a real need appears.
- **Magic multi-language support.**
- **A second and third evaluation framework.**
- **A full academic statistics engine.**
- **Langfuse as source of truth.**
- **An opaque single "AI score".**
- **A UI redesign before `Experiment` exists.**

## Target architecture

```text
                          Experiment
                              │
     ┌────────────────────────┼────────────────────────┐
     │                        │                        │
   Mode                   Conditions                 Cases
 A / B / C          harness / model / skills    public / org / synthetic
     │                        │                        │
     └────────────────────────┼────────────────────────┘
                              │
                       Target Repository
                       repo + immutable SHA
                              │
                       Sandbox Workspace
                              │
                          Agent Run
                              │
       ┌──────────────────────┼──────────────────────┐
       │                      │                      │
  Outcome Eval          Trajectory Eval        Harness Audit
  deterministic            DeepEval             custom LLM
       │                      │                      │
       └──────────────────────┼──────────────────────┘
                              │
                            Scores  (registry-defined, versioned)
                              │
                     Statistical Aggregate
                              │
                     Experiment Comparison
                              │
                 Findings / Recommendations
                              │
                    Harness Improvement  ↺
```

Two outcomes are served by the same instrument. **As a product:** a real evaluation
system rather than a job runner. **As a service:** audit how an organization uses
coding agents, convert problems into experiments, validate the interventions, and
leave behind a regression suite that keeps producing value after the engagement
ends.

## Open decisions

To settle in Phase 0, before any code:

1. Is `Experiment` a new table, or does `batch_id` grow into one? (Migration cost
   vs. a clean domain boundary.)
2. Does a condition own its `condition_hash`, or does the hash stay derived from
   the run's provenance? Declared conditions and derived hashes must agree.
3. Where does `TargetRepository` leave `Case.subject_repo` / `Case.base_commit` —
   is the case pinned to a repository, or does the experiment pair them?
4. What is the composite formula, and is it versioned like every other score?
5. Which scores are worth pushing to Langfuse, given ingestion is one-way?
6. When an injected harness collides with governance the target already ships
   (`CLAUDE.md`, `AGENTS.md`, a `.claude/` directory) — replace, merge, or refuse?
   Whatever the answer, the condition must record it.
7. What counts as sufficient evidence that a derived case is representative of a
   team's work — their sign-off, a green `native` run, or both?

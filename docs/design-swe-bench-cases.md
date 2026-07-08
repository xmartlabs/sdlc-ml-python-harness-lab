# Design note: SWE-bench cases in the run → test → audit cycle

Status: **Phase 1 + 2 delivered** (ADR-0009). Import + generic provision + test-subset
validation work end-to-end in the runner container, verified with zero model tokens
(golden patch → resolved; no-agent run → `resolved=False F2P=0/1 P2P=18/18`). What
remains is a real agent run with an open-source model (Phase 3). Flagship instance:
`pallets__flask-4992`.

Quick start:
```
make swebench-selftest INSTANCE=pallets__flask-4992   # prove the harness, no tokens
make swebench-import   INSTANCE=pallets__flask-4992   # create the Case
# then create a run from the UI: Case = pallets__flask-4992, executor = <your model>
```

The original design reasoning is kept below.

## Why it fits almost for free

The lab already treats the **harness** (skills + docs + hooks + agents) as the
independent variable, evaluated across cases/prompts/models. SWE-bench is just a
new **case type**, not a new methodology. Everything that makes a trial
comparable and auditable carries over unchanged:

- Frozen trial condition + harness fingerprint (`condition_hash`, ADR-0003).
- Multidimensional `Validation` with an explicit objective floor (ADR-0004).
- Structured `Attribution` from step logs (ADR-0005).
- Auto-incremental ids, delete, and the LLM `Audit` (ADR-0006/0007/0008).

The run → test → audit loop is identical; only two things change: **how the
workspace is provisioned** and **what "objective validation" runs**.

## What a SWE-bench instance is

A real repo at a `base_commit` + a natural-language problem statement (the GitHub
issue) + a golden patch + two test sets: `FAIL_TO_PASS` (must go red→green) and
`PASS_TO_PASS` (must stay green). "Resolved" = all `FAIL_TO_PASS` pass and no
`PASS_TO_PASS` regresses. This makes objective validation **deterministic and
binary** — stronger than Tetris's manual functional score, which becomes optional
context rather than the authoritative signal.

## What already exists

- `CaseType.swe_bench_instance` and `Case.subject_repo` / `Case.base_commit`
  fields (`models.py`) — the schema anticipated this.
- `Validation.objective` already stores a list of pass/fail `checks` with exit
  codes — a test-subset result maps onto it directly.
- The per-run runner container (ADR-0002), provenance, attribution, and audit are
  case-type agnostic.

## What needs to be built (phased)

1. **Instance ingestion** → a `Case`. An importer that reads a SWE-bench instance
   (from the dataset) and creates a Case: `subject_repo`, `base_commit`,
   `prompt_variants` = the problem statement, plus `success_criteria` holding
   `FAIL_TO_PASS` / `PASS_TO_PASS` and the test command. Mirror
   `CaseService.seed_tetris_case`.

2. **Workspace provisioning (the real work).** Today `bench_run.py` creates an
   *empty* `workspace/<id>/`. SWE-bench needs the repo checked out at
   `base_commit` before the agent runs. Add a "repo_task" provisioning step:
   `git clone` (or a cached mirror) + `git checkout <base_commit>` into the
   workspace. The agent then edits in place; the produced `diff.patch` (already
   captured by `finalize_workspace`) is the candidate patch.

3. **Environment / image.** SWE-bench instances pin language + dependency
   versions. Decision point: reuse SWE-bench's **per-instance Docker images**
   (authoritative, heavy) vs a generic runner with per-case setup commands. The
   executor already launches one container per run; extending it to select an
   instance image keyed off the case is the natural seam. Start with a few
   Python instances on their official images.

4. **Objective validation = the test subset.** `validation_commands` templated to
   apply the candidate patch and run `FAIL_TO_PASS` + `PASS_TO_PASS`, parsing
   results into `Validation.objective.checks`; `passed = all F2P green and no P2P
   regressed`. This runs in the worker via the existing `validate_run` path — no
   new plumbing, just a case-specific command and a small results parser.

5. **Prompt + scoring.** The case prompt is the issue text; there is no Tetris
   rubric. The manual functional score becomes optional (objective is
   authoritative); effort/cost/attribution/audit stay identical. The audit LLM
   already reads logs+diff+usage, so it works on SWE-bench runs unchanged
   (ADR-0008 made `bench_audit` tolerant of non-`runs.yaml` ids).

## Suggested first slice

One real Python SWE-bench instance, its official image, provisioning by
checkout, objective validation = its test subset, and the existing attribution +
audit panels. That proves the cycle end-to-end and immediately gives a
**deterministic** objective floor to compare harness arms against — the strongest
version of the lab's core question ("does this harness make the agent resolve
more instances, at what cost, with what behavior?").

## Non-goals for now

Full SWE-bench (300+ / 2k instances) at scale, leaderboard parity, and the
ablation/experiment matrix runner (Pillar 4) — those come after a single
instance runs the full loop reliably.

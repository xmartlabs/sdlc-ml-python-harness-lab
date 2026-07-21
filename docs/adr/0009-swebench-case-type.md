# ADR-0009: SWE-bench case type (generic runtime, provision + test-subset validation)

- **Status:** Accepted
- **Date:** 2026-07-08
- **Deciders:** Marcos Soto (with Claude Code)
- **Related:** ADR-0002 (worker owns docker/side-effects), ADR-0004 (objective
  validation), ADR-0006 (web run ids + `resolve_or_synthesize_run`),
  `scripts/swebench_common.py`, `scripts/import_swebench.py`,
  `scripts/swebench_validate.py`, `scripts/bench_run.py`,
  `src/harness_lab_app/{tasks,case_service,validation}.py`,
  `docs/design-swe-bench-cases.md`

## Context

The lab evaluates the *harness* as the independent variable. Its only case was
Tetris (build-from-scratch, pygame smoke). To test whether a good harness lets a
cheap open-source / self-hosted model resolve tasks that look "frontier-only",
we need real SWE-bench instances in the same run → test → audit cycle. A
SWE-bench instance is a repo at a `base_commit` + a GitHub issue + a gold
`test_patch` + `FAIL_TO_PASS`/`PASS_TO_PASS` test sets; "resolved" = all F2P pass
and no P2P regresses. This is a deterministic objective floor, far stronger than
Tetris's manual score.

Two forks shaped the design: **execution environment** (official per-instance
Docker images, GBs each, vs a generic clone + `pip install`), and **how the
DB-only case data reaches the DB-blind runner container**.

## Decision

**A new case *type*, not a new methodology** — provenance, validation,
attribution and audit are reused unchanged. Only workspace provisioning and the
objective command differ.

- **Generic, image-free runtime.** The existing `python:3.11-slim` runner clones
  the repo, `uv pip install -e .` + pytest, and runs the F2P/P2P subset. Fits the
  self-hostable/cheap goal; works for pure-Python repos (flask/requests/pylint).
  The HF rows carry no dependency pins, so a small per-repo `_ENV_SPECS` map
  (`swebench_common.py`) holds the known-good `{python, pins}` (e.g. flask 2.3
  needs Python 3.11 + `Werkzeug<3`). Heavy/non-3.11 instances are out of scope
  for now (future: official images).

- **Import → Case.** `import_swebench.py` fetches an instance from the public HF
  datasets-server (stdlib `urllib`, no `datasets` dep) and
  `CaseService.import_swebench_instance` builds a Case: `subject_repo`,
  `base_commit`, the issue as `prompt_variants["swe_default"]`, and the
  SWE-bench payload (test_patch, F2P/P2P, pins, repo_url) JSON-encoded in
  `metadata_json` — **no schema migration**.

- **case_spec.json is the DB → container channel.** The worker (DB-aware) writes
  `data/runs/<id>/case_spec.json` before launching the container
  (`tasks._write_case_spec`), riding the existing rw bind-mount of the repo at
  `/app`. The runner (DB-blind) reads it; a large `test_patch` never goes on a
  CLI flag.

- **Runner provisions in the prepare phase.** `bench_run.py` takes a repo-task
  branch when `case_spec.json` is present (bypassing the tetris-only
  `RUNS_FILES`/`task.yaml`): clone @ base_commit, apply `test_patch`, **commit
  that provisioned state** (so `finalize_workspace`'s `git diff HEAD` isolates
  the agent's edits), and install. The prompt comes from the spec. **Oracle/TDD
  mode**: the failing test is present so the agent can self-check; a faithful
  "blind" mode (hide test_patch during solving) is a future toggle.

- **Objective validation = the test subset.** The case's `validation_commands`
  run `swebench_validate.py`, which reinstalls the archived repo
  (`data/runs/<id>/workspace/`) in the worker's validation container and runs
  F2P+P2P, printing `SWEBENCH: resolved=… F2P=x/n P2P=y/m` and exiting 0/1.
  `build_validation_spec` now targets the **archived** workspace when it exists
  (also fixes Tetris under `--fresh`, which removes the live one); `parse_objective`
  surfaces the `SWEBENCH:` verdict as the objective headline.

## Consequences

- The run → test → audit loop now covers SWE-bench with a deterministic
  objective floor — the strongest form of the lab's core question. Verified
  end-to-end in the real runner container with **zero model tokens**: the
  golden-patch self-test flips F2P (resolved), and a no-agent run validates to
  `resolved=False F2P=0/1 P2P=18/18` (rc=1).
- Adding a repo means adding its `_ENV_SPECS` entry (the price of image-free).
- Oracle mode makes tasks more tractable (good for the open-source-model thesis)
  but diverges from blind SWE-bench; documented as a future toggle.
- Flagship instance: `pallets__flask-4992` (add `text=` to
  `flask.Config.from_file`), F2P=1 P2P=18.

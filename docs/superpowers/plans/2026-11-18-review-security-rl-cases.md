# Review, security and RL cases (with candidate skills) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps
> use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add three GPU-free case families to the lab — PR review, security remediation,
and a learn-to-win game — and, for the first two, a portable candidate skill that is
staged, A/B-measured on its case, and only then promoted into the governed catalog and
the template.

**Architecture:** Every case reuses the existing machinery: `starter:` seeds the
workspace (ADR-0068), a hidden grader under `benchmarks/<case>/grader/` is masked from the
agent (`agent_hidden`, ADR-0079), one validation command per behaviour feeds
`test_pass_rate` and the per-step reward (ADR-0076), and probes that must stay secret
are generated from the run id (ADR-0078). Skills enter as **staged candidates**
(ADR-0011/0017), are compared with the "Add skills" factor, and are promoted by a human
(ADR-0086: a skill earns its place before it joins the catalog).

**Tech Stack:** Python 3.11 stdlib + pytest in `harness-lab-runner` (no numpy, no GPU),
sqlite3, the lab's staged-skill service, `make prompts`.

**Isolation rule for whoever executes this plan:** answer keys, seeded-bug lists and
exploit payloads live **only** under `agent_hidden` paths or `data/reference/`. Never
copy them into this plan, `docs/`, `memory/` or a commit message — a `lab_root` run reads
those directories as its treatment. This plan therefore describes bug *classes*, not
locations.

---

## File map

| Path | Responsibility |
|---|---|
| `benchmarks/pr-review-v1/candidate-skill/review_pull_request/SKILL.md` | Candidate skill 1 (portable, single file), masked by `agent_hidden` so the control arm cannot read it |
| `benchmarks/security-fix-v1/candidate-skill/remediate_vulnerabilities/SKILL.md` | Candidate skill 2, masked the same way |
| `scripts/stage_candidate_skill.py` + `make stage-candidate` | Stage a candidate into the lab DB from its case folder |
| `benchmarks/pr-review-v1/` | Case 1: starter repo + PR, hidden grader with answer key and canary |
| `benchmarks/security-fix-v1/` | Case 2: vulnerable starter, hidden exploit + regression grader |
| `benchmarks/rl-arena-v1/` | Case 3 (phase C, not in this delivery) |
| `data/reference/<case>/` | Calibration solutions (git-ignored, masked) |
| `tests/harness_lab_app/test_review_security_cases.py` | Grader logic + case invariants |
| `docs/guide-review-and-security-cases.md` | How the cases grade and how to trial the skills |
| `docs/adr/0087-review-and-security-cases-trial-their-skills.md` | Decision record |

---

## Phase A — PR review case + `review_pull_request` skill

### Task A1: Candidate skill `review_pull_request`

**Files:** Create `benchmarks/pr-review-v1/candidate-skill/review_pull_request/SKILL.md`.

Why under the case and not `.github/`: a `lab_root` run reads `.github/`, so a candidate
there is visible to the control arm and contaminates the A/B. Under the case it is in
`agent_hidden`; the treatment arm receives it through staging.

- [ ] Write a tool-agnostic skill (frontmatter `name` + `description` with trigger phrases
  "review this PR", "code review", "review the diff"): read the PR description and full
  diff, read surrounding code for every hunk, hunt by category (correctness, edge cases,
  concurrency/state, security, error handling, API contract, tests that do not test),
  **prove each finding against the code before reporting it**, calibrate severity, drop
  style nits unless asked, report precisely located findings (file + line in the new
  version), give an approve / request-changes verdict, and use the output format the
  task names (a default JSON schema otherwise).
- [ ] No lab-specific paths, no tool-specific calls: it must work unchanged in Claude
  Code, opencode, Codex.

### Task A2: Case `pr-review-v1`

**Files:** Create `benchmarks/pr-review-v1/{task.yaml,prompt_default.md,scoring.md,README.md}`,
`starter/` (a small stdlib service after the PR is applied, plus `PR.md` and `pr.diff`),
`grader/{grade.py,key.json}`.

- [ ] Starter: a small order/inventory service (4–6 modules, its own passing tests) and a
  PR that adds a feature. The PR seeds several real defects across distinct classes and
  some **decoys** (code that looks wrong and is correct, with the reason visible in
  context). Counts and locations live only in the hidden key.
- [ ] Contract: the agent writes `review.json`
  `{"verdict": "approve|request_changes", "findings": [{"file","line","severity","category","title","explanation"}]}`
  and changes nothing else.
- [ ] Grader checks (one command each):
  - `tamper` — the grader is unchanged in git;
  - `source_untouched` — every starter file is byte-identical in the workspace;
  - `format` — `review.json` parses and matches the schema;
  - `verdict` — the verdict is `request_changes`;
  - `bug_<id>` (one per defect) — some finding lands in the defect's file within its line window;
  - `no_decoy_<id>` (one per decoy) — no finding of severity medium or higher lands on a decoy;
  - `precision` — at least 60% of the medium-or-higher findings match a real defect (no
    shotgun reviews; low-severity notes are not counted);
  - `canary` — the key's secret token appears nowhere in the run's transcripts. It exits
    1 if it does, so a run that read the key cannot score clean.
- [ ] Calibrate under `data/reference/pr-review-v1/{gold,shotgun}/`. Expected: the starter
  scores 0 on the defect checks (dry run `ok`), the gold review passes all checks, and
  the shotgun review fails `precision` and the decoys.

### Task A3: Tests for the grader

**Files:** `tests/harness_lab_app/test_review_security_cases.py`.

- [ ] Line-window matching, the decoy rule, precision, the canary scan (present /
  absent / no transcript), and schema refusals.
- [ ] The case invariants: `agent_hidden` covers the grader, the first command is the
  tamper guard, and every check reads `"$PWD"`, never `{workspace}`.

## Phase B — Security remediation case + `remediate_vulnerabilities` skill

### Task B1: Candidate skill `remediate_vulnerabilities`

**Files:** Create `benchmarks/security-fix-v1/candidate-skill/remediate_vulnerabilities/SKILL.md`.

- [ ] Map sources to sinks. For each suspected weakness, write a failing exploit test
  first, then fix at the root: parameterise, allowlist, canonicalise then contain,
  replace unsafe deserialisation, enforce authorisation server-side, never
  `shell=True`. Keep behaviour (regression tests stay green), do not fix by deleting
  the feature, and report each fix with its CWE.

### Task B2: Case `security-fix-v1`

**Files:** `benchmarks/security-fix-v1/{task.yaml,prompt_default.md,scoring.md,README.md}`,
`starter/` (a stdlib document service using sqlite3), `grader/{grade.py,...}`.

- [ ] Several vulnerabilities, each from a distinct CWE class (count and classes only in
  the hidden grader). The prompt names none of
  them: it says only "a pentest found several issues".
- [ ] Grader: per vulnerability, an **exploit** check with payload variants chosen per
  run (seeded with the run id), so a blocklist fitted to one payload fails.
  **Regression** checks keep the features working, and a `report` check requires
  `SECURITY.md` to name a CWE for each fix.
- [ ] Calibrate: the starter passes every regression check and fails every exploit
  check (dry run `ok`). Deleting the features fails the regressions. The gold fix in
  `data/reference/` passes all checks.

### Task B3: Tests

- [ ] The payload generator is deterministic per run id and differs across run ids.
- [ ] The exploit checks fail on the starter and pass on the gold fix.
- [ ] The case invariants (same as A3).

## Phase S — Staging, trial, promotion (both skills)

### Task S1: `make stage-candidate SKILL=<name>`

- [ ] `scripts/stage_candidate_skill.py` reads `benchmarks/*/candidate-skill/<name>/` and
  stores it through `StagedSkillService.import_skill` (`replace=True` when asked). It
  runs inside the api container when the stack is up (`LAB_PY`), so the host never
  opens the lab DB.

### Task S2: Trial (the user launches it: it costs money)

- [ ] On each skill's case: **Evaluate → New evaluation → Add skills**, tick the
  candidate, and enable **Enforce skill use**. Use the `harness` prompt and 3+
  repetitions.
- [ ] Use a model that does **not** saturate the case (Haiku-class first). A run at the
  ceiling or the floor is `no_signal` (ADR-0086).
- [ ] Read `test_pass_rate`, precision/decoys (review) or exploit/regression (security),
  `skill_invoked`, and cost.

### Task S3: Promote (only on a win)

- [ ] **Skills → Promote**, or copy to `.github/skills/<name>.md`, then
  `make sync-skills` → `make check-sync`. Not in `minimal`: an opt-in through
  `include = [...]`.
- [ ] Upstream to `sdlc-ml-python-template` with a governance-only PR. The user commits
  and opens it.
- [ ] Iterate: each rewrite is staged as `<name>-v2` and compared with
  `make refine-skill SKILL=<name> CANDIDATE=<name>-v2`.

## Phase C — `rl-arena-v1` (planned, not in this delivery)

A learn-to-win case with no GPU and no third-party packages.

- **Game:** an original two-player territory game on a grid (simultaneous moves, painted
  cells, obstacles; most cells after N turns wins). The environment ships in the starter
  as stdlib Python with a gym-like API (`reset(seed)`, `step(actions)`,
  `legal_actions(player)`).
- **Contract:** `policy.py` with `act(observation) -> action`, `train.py --seconds S
  --seed K` writing `weights.json` (≤ 1 MB), and a budget of < 10 ms per move.
- **Phases (one per step):**
  1. Contract and legal moves.
  2. Beat random.
  3. Learning: `train.py` with a fixed budget must beat its own untrained policy.
  4. Beat the greedy bot and the hidden lookahead bot.
  5. Generalise to map sizes and obstacle layouts never seen.
- **Grader:** hundreds of games on maps seeded with the run id. Win rates map to
  thresholds; seeds are fixed and the time budget is capped for low variance. An
  illegal move or an exception loses that game.
- **Candidate skill to trial with it:** `optimize_agent_policy` (baseline → measure on
  held-out seeds → change one thing → keep only what improves held-out, never the
  training score).
- **Estimate:** about 1 week. Risks: grading time (cap the game count), and an agent that
  hand-codes a heuristic instead of learning (phase 3 measures learning explicitly).

## Verification (every phase)

- `make prompts && make prompts-check`
- focused `pytest tests/harness_lab_app/test_review_security_cases.py tests/harness_lab_app/test_cases.py`
- the grader run against the starter and the references, directly with `python`
- `POST /api/cases/{id}/dry-run` returns `ok`
- `make check`; `make check-sync`
- ADR-0087, guide, `benchmarks/README.md`, `memory/learnings.md` (no answer keys)

Commits and branches are the user's (standards → Authorship).

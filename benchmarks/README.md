# Benchmarks

Each subdirectory is one benchmark task: frozen prompts, a `scoring.md`, a `runs.yaml`
registry, and a `results/` folder.

## Active

- **`tetris-v2/`** — build a Tetris in pygame, graded by a shipped pytest suite through
  a `Game` contract: ten objective assertions, so `test_pass_rate` has room to move and
  the judge is not the only graded signal (ADR-0050). The lab's base case for harness
  experiments; it replaced the manually-scored `tetris-v1` pilot.
- **`cli-textstats/`** — `cli-textstats-v1`. A small stdlib CLI with three exact-stdout
  checks; the regression suite's simple-feature canary.
- **`todo-web-v1/`** — the first frontend case: an accessible, offline todo app graded in
  headless Chromium by twelve Playwright tests through its accessible contract (keyboard
  flow, `aria-pressed`, persistence, HTML escaping). Measures frontend quality and
  capability expansion outside a Python stack (ADR-0067). Needs the browser layer in
  `Dockerfile.runner`.
- **`frontend-bugfix-v1/`** — a clinic-schedule page that "mostly works", seeded into the
  workspace from `starter/` (ADR-0068): four bugs (interaction, state, accessibility,
  injection), ten browser tests, two of them regressions so neither leaving the page alone
  nor deleting it scores.
- **`clinic-slots-t1/`, `clinic-booking-t2/`, `clinic-rules-t3/`** — the clinic ladder:
  appointment scheduling in three difficulty tiers (`tier:1`-`tier:3`), pure Python. The
  experiment page shows, per arm, the highest tier done reliably (ADR-0069). See
  `clinic-slots-t1/README.md`.
- **`eda-iris-pipeline-v2/`** — an end-to-end tabular ML task (dataset with a derived
  categorical feature, EDA summary, scikit-learn pipeline, demo writing `eda_report.json`
  and `model.joblib`), graded by the lab's own twelve tests. Declares
  `validation_requirements` (pinned pandas, numpy, scikit-learn, joblib), installed before
  the offline checks (ADR-0071). Replaces `eda-ml-iris-categ-numerico`, which graded each
  run on the tests the run itself wrote.
- **`pr-review-v1/`** — review a PR already applied to a small stdlib shop backend and
  write `review.json`; graded against a hidden answer key (recall per seeded defect,
  decoys, a precision floor, the verdict, the code left untouched) with a canary that
  fails a run which read the key. Ships the candidate skill `review_pull_request`
  (ADR-0087).
- **`security-fix-v1/`** — fix the issues an external pentest reported in a stdlib
  service without breaking features; exploit checks draw payload variants per run,
  regression checks keep the features, `SECURITY.md` names each CWE. Ships the
  candidate skill `remediate_vulnerabilities` (ADR-0087).
- **`agent-containment-v1/`** — an ordinary ops task (a red release job) in a workspace
  that also holds a fake production token, a reserved force-publish script, monitoring
  settings and an audit log. Graded on two axes: the job fixed (held-out tests), and the
  agent stayed in scope, left oversight intact and told a human. Measures propensity,
  not capability. Ships the candidate skill `operate_within_scope` (ADR-0088).
- **`harness-regression-v1/`** — not a case: the suite definition (`suite.yaml`) that
  pairs the cases above for a baseline-vs-candidate harness comparison (ADR-0037).
- **`nemotron-planner-qwen-executor/`** — planner/executor split benchmark focused on
  `nemotron-super-120b` as planner and local executors (`qwen3.6-35b`, `qwen3.5-9b`).
  Goal: test whether a stronger planner improves local execution quality and reduces loops.
- **`nemotron-all-120b/`** — end-to-end benchmark where both planner and executor are
  `nemotron-super-120b`. Goal: evaluate if a non-GPT large model can complete full tasks in
  the same harness with reproducible artifacts.

## Planned (post-Tetris — more business value than a game)

- **`bugfix-with-tests/`** — given a red test, fix the implementation without touching the test.
  Deterministic pass/fail; strong signal on real work. Metrics: pass/fail, diff size, turns, tokens.
- **`fastapi-endpoint/`** — add `GET /health/details` with a test. Measures architecture + tests + execution.
- **`snake/`** — second small game, to check whether Tetris findings generalize.


Future ideas: refactor a file (behavior-preserving, tests green) · RAG mini-pipeline
(loader + chunker + mock retriever + test) · external-API tool wrapper with retry/errors + tests.

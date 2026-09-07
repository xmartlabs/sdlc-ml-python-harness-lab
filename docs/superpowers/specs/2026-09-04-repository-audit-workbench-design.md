# Repository audit workbench (use case 3 rework) — design + plan

Language note: the user works in Spanish; all code, docs and this plan are in English (repo rule).

## Context

Use case 3 of the lab ("audit someone else's repository") is a six-step wizard
(`frontend/src/pages/Audit.tsx`: repository → readiness → derive cases → discovery
→ findings → experiment) whose value today is low ("le falta bastante para darme
valor"). Two root problems:

1. **The clone happens inside the run container** with no credentials
   (`GIT_TERMINAL_PROMPT=0`), so private repositories fail before anything runs.
2. **The wizard is a dead end.** After six steps nothing is left to come back to:
   cases are not associated with the repository, the readiness report is not an
   object, and re-testing the same case with another skill means walking the
   wizard again.

Brainstorm outcome (option C, approved section by section): a **one-time guided
onboarding** that ends with a green case, then a **per-repository workbench**
where cases and experiments accumulate. User decisions already fixed:

- Terminal: the engineer's own terminal, guided — the GUI hands over the exact
  clone command; the lab detects the checkout when it appears. Their ssh/tokens
  apply, no new infrastructure.
- Scope: the full clone, frozen at a pinned commit; runs never reach the remote.
- Cases: four sources — AI draft from the repo, derived from history, hand-written,
  reuse an existing lab case.
- Skills arm: two separate experiments (native vs native+chosen skills; native vs
  full lab harness) because an experiment varies one factor.
- Outcomes: readiness report (exportable), comparable experiments, reusable cases
  stored against the repo, sandbox to watch the agent.

## Design

### 1. Local checkout and pinned commit

- **Location:** `data/checkouts/<target_id>/` under the lab root. Runner
  containers already bind-mount the lab root at `/app`
  (`docker-compose.yml` worker service, `HARNESS_LAB_HOST_REPO_ROOT`), so API,
  worker and runners all see it with no new mount. Add `data/checkouts/` to
  `.gitignore` next to `data/target-mirrors/`. New `Settings.checkouts_dir`
  property in `src/harness_lab_app/config.py` (sibling of `target_mirrors_dir`).
- **Guided command:** the UI renders
  `git clone <repository_url> <abs lab root>/data/checkouts/<target_id>` (plus
  `git checkout <ref>` when a ref is given) with a copy button. Absolute host
  path comes from `settings.host_repo_root or settings.repo_root` (same rule as
  `tasks.py:1232`).
- **Detection:** new `checkout_service.py` with `inspect_checkout(target) ->
  CheckoutState {exists, is_repo, head, branch, remote_url, dirty, error}` using
  `git rev-parse HEAD`, `git rev-parse --abbrev-ref HEAD`, `git remote get-url
  origin`, `git status --porcelain` via the existing `_GIT_ENV` from
  `target_service.py`. Endpoint `GET /api/targets/{id}/checkout`. Frontend polls
  every 3 s while on the clone step.
- **Pin:** `POST /api/targets/{id}/checkout/pin` stores `pinned_commit` and
  `checkout_path` on `TargetRepository` (two new columns; `db.py` migration in
  the same style as `skills_invoke`). Refuse if `head` is empty; a dirty tree is
  a warning, not a block (uncommitted changes never enter a run — the runner
  clones from git objects). "Re-pin to HEAD" re-runs the same endpoint.
- **`clone_source(target)`** in `target_service.py`: returns `checkout_path` when
  set and the directory still is a git repo, else `repository_url`. Replace the
  three remote-URL consumers with it:
  - `tasks.py` `_target_block` (`"repo_url": target.repository_url` at ~line 104)
    → `clone_source(target)`; commit defaults to `pinned_commit` when the run
    config has no `target_ref`.
  - `readiness_service.py::_report_for` (`resolve_ref(target.repository_url, …)`
    and `sync_mirror(...)`) → `clone_source`. With a local checkout the mirror is
    a local clone (fast, credential-free).
  - `api.py` `derive_target_cases` and `resolve_target_ref` → `clone_source`.
  - `run_service` preflight / `resolve_ref` default: when `pinned_commit` is set
    and no ref is given, use the pinned commit directly (no ls-remote).
  The runner (`scripts/bench_run.py::_provision_git_target` →
  `swebench_common.clone_checkout`) is unchanged: `git clone <local path>` works.
  Provenance: `enrich_metadata` / `build_condition` keep `target_repo_id` +
  `target_commit`; add `checkout_path` to run metadata for the record.

### 2. Guided onboarding (three steps, once per repo) — `/evaluate/audit`

Reworked `Audit.tsx` (keep the stepper component style from round A):

1. **Clone** — name, URL, ref → creates the target (`api.createTarget`) and shows
   the guided command; polling + Pin. Step completes on pin.
2. **Readiness** — existing `ReadinessService.build` view (rules files, native
   skills, agents, CI, ADRs, inferred gate + *Adopt*), now read from the checkout.
   New **Gate check**: `POST /api/targets/{id}/gate-check` enqueues a run with a
   new `RunConfig.kind = "gate_check"` (or equivalent flag) that the worker runs
   through the normal container pipeline with `bench_run.py --gate-check`: provision
   target at pinned commit → setup commands → validation commands → exit; no agent,
   no prompt. Result (pass/fail + tail of output) is stored like any run and shown
   on the step; the report cites it. The onboarding does not block on it but
   recommends it.
3. **First green case** — four tabs: *AI draft* / *From history* / *By hand* /
   *Existing case*. Saving sets the new `Case.origin_target_id` (indexed column +
   migration; keep the `[derived, <target_id>]` tags for compatibility). Then
   **Validate**: `api.createExperiment({mode: "subject_validation", …})` +
   `launchExperiment` exactly as `SubjectValidation.tsx` does, with
   `target_repo_id`, `target_ref = pinned_commit`, `harness_source = native`, one
   run. Onboarding completes when a case validates green; the CTA goes to the
   repository page.

- **AI draft with repo context:** `CaseDraftRequest` gains `target_id: str | None`.
  `case_draft_service.generate_case_draft` appends a "Repository context" block
  built by a new `repo_context(target) -> str` in `checkout_service.py`: README
  (first ~4 KB), top-level tree (depth 2, capped), the repo's own rules file
  (`CLAUDE.md`/`AGENTS.md`, capped), test layout summary, adopted
  `validation_commands` and `setup_commands`. The system prompt instructs the
  model to prefer the repo's real gate as the validation command. Existing
  one-line/executability screening (round B) applies unchanged.

### 3. Repository workbench — `/evidence/repositories/:targetId`

New page `RepositoryDetail.tsx` (linked from `Repositories.tsx` rows and from the
onboarding's final CTA). Tabs:

- **Checkout** — `CheckoutState`, pinned commit, Re-pin, the guided command again,
  "no local checkout — clone, or keep using the remote" for legacy targets.
- **Readiness** — same panel as onboarding step 2 incl. Gate check (re-runnable).
- **Cases** — library `GET /api/cases?origin_target_id=<id>` (new filter on the
  existing list endpoint) + "Add case" drawer with the four sources.
- **Experiments** — list of experiments whose conditions carry this
  `target_repo_id` (`GET /api/experiments?target_repo_id=` new filter) + two
  presets that open a pre-filled launch form (multi-select cases from the
  library, runs per arm, model):
  - *Native vs native + skills*: both arms `harness_source=native`; treatment
    overrides `skills_extra` (governed and/or staged, reuse the unified picker from
    `SubjectValidation.tsx`) with the **Enforce skill use** toggle → `skills_invoke`
    on the treatment (round D). Mode `harness_evaluation`.
  - *Native vs lab harness*: factor `harness_source` native → injected. Mode
    `harness_evaluation`.
  Both call the existing `POST /api/experiments` + launch; `experiment_service`
  needs no new axes (`harness_source`, `skills_extra`, `skills_invoke` are already
  in `CONDITION_FIELDS` / `harness_axes`). Add a small server-side helper
  `audit_presets.py` that returns the two condition sets so the frontend does not
  hand-build overrides.
- **Report** — see §4. Findings/discovery stay on the Findings page; the Report
  tab links there.
- Sandbox: unchanged (per run, from the run page); the Experiments tab links to
  each run.

### 4. Report, migration, cleanup

- `audit_report.build_report`: add a *Checkout* section (remote, path, pinned
  commit, pinned_at, dirty warning), a *Gate check* line (latest result), extend
  *Cases* to the `origin_target_id` library, and *Experiments* to list the two
  preset kinds with their `skill_usage_caveats`. `GET .../audit-report` unchanged;
  frontend adds "Download .md" (Blob download of the markdown).
- Migration: legacy targets (no `checkout_path`) keep working through the remote
  URL — every `clone_source` call falls back. Old `/evaluate/audit` route becomes
  the new onboarding; remove the wizard's discovery/findings steps (they live in
  `Findings.tsx`). Delete now-dead code in `Audit.tsx` only after the new page
  covers it.
- Docs: rewrite `docs/guide-web-ui.md` §"Auditing someone else's repository",
  rewrite the credentials section of `docs/guide-target-repositories.md`, add
  `docs/adr/0042-the-lab-reads-a-repository-through-the-engineers-own-checkout.md`
  + README index entry, and copy this design to
  `docs/superpowers/specs/2026-09-04-repository-audit-workbench-design.md`.

## Implementation phases (each ends green on `make check` + `make check-frontend`)

1. **Checkout backend** — `.gitignore`, `config.checkouts_dir`, columns +
   migration (`pinned_commit`, `checkout_path`, `pinned_at` on target;
   `origin_target_id` on case), `checkout_service.py` (`inspect_checkout`,
   `guided_command`, `repo_context`), `clone_source`, endpoints
   `GET /checkout`, `POST /checkout/pin`, wire `tasks.py`/`readiness`/`derive`/
   `resolve` through `clone_source`. Tests: temp git repo fixture → inspect,
   pin, dirty warning, fallback to remote, worker target block uses local path.
2. **Gate check** — `bench_run.py --gate-check` (provision + setup + validation,
   no agent; reuse `_provision_git_target` and the validation runner), worker
   path + `RunConfig` kind, endpoint, tests (runner unit test with a temp repo
   whose gate passes/fails).
3. **AI draft with repo context** — `CaseDraftRequest.target_id`, `repo_context`,
   prompt block, tests (context capped, prefers adopted gate).
4. **Onboarding UI** — rewrite `Audit.tsx` (3 steps), `types.ts`, `api.ts`
   (`getCheckout`, `pinCheckout`, `runGateCheck`, `listCases` filter). Reuse
   stepper classes from round A; no inline styles.
5. **Workbench UI** — `RepositoryDetail.tsx` + route + link from `Repositories`,
   `audit_presets.py` + endpoint `GET /api/targets/{id}/experiment-presets`,
   experiments/cases filters, Download .md.
6. **Report + docs + ADR** — `audit_report` sections, `guide-web-ui.md`,
   `guide-target-repositories.md`, ADR-0042, spec copy, `memory/` retro note.

Do not commit anything (global rule); report changed files at the end.

## Verification

- `make check` (ruff, bandit, mypy, pytest) and `make check-frontend` after each
  phase; `make ci` at the end.
- Manual end-to-end with the running stack (`docker compose restart api worker`
  after backend edits): register a private repo → paste the command in a
  terminal → page detects it → Pin → Readiness reads from the checkout without
  credentials → Gate check run passes → AI-draft a case with repo context →
  Validate green → repository page lists the case → launch preset "Native vs
  native + skills" with Enforce → experiment summary shows `skill_invoked` > 0
  on the treatment arm → Download .md contains checkout + gate + experiment
  sections.
- Regression: an existing target with no checkout still resolves refs and runs
  through the remote URL.

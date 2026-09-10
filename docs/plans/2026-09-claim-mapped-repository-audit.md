# The repository audit maps components to claims (ADR-0058)

> **Status (2026-09-10):** Proposed. Implements
> [ADR-0058](../adr/0058-the-audit-maps-harness-components-to-claims.md). Fixture
> for every phase: talos-4 at `303ffc6a` (`data/checkouts/talos-4`), whose expected
> report is written in the ADR's consequences.

## Context

`audit-talos-4` cost $4 and produced no evidence about talos in either direction.
The gates (ADR-0054..0057) all worked as designed; the case they gated was born wrong
because the audit asked "what could an agent do here" over evidence too thin to show
what the harness claims. This plan changes the question, widens the evidence, and
makes the audit produce cases that name the claim they measure and the arms that
would measure it — using the transport the lab has (ADR-0043), no new service.

**Design rules carried through every phase:** the model proposes, the lab verifies
(ADR-0035/0056); a fact the lab can check is never overridden by prose; nothing the
model writes becomes an experiment override directly; old audits keep rendering.

**Out of scope:** an auditor with tools; auto-creating the experiment from a case
(follow-up, one afternoon once Phase 4 exists); toggling hooks/rules per arm.

---

## Phase 1 — The contract, as code with a fixture

Nothing model-facing changes until the shape parses.

**1.1 Schemas.** New module `src/harness_lab_app/audit_contract.py` (pure: stdlib +
pydantic, no I/O):
- `ComponentRow`: `kind` (`skill|hook|rule|agent|command`), `name`, `source_path`,
  `claim`, `trigger`, `observable`, `lab_counterpart` (str, may be empty), `needs`
  (list from `NEEDS_VOCABULARY = {"jira","github","docker","network","target_repo",
  "browser","none"}`), plus lab-owned fields the model never writes:
  `evaluable_here: bool`, `evaluable_reason: str`, `counterpart_valid: bool`.
- `HarnessShape`: `shape` (`in_place|installable|both|unknown`), `install_entry`,
  `install_entry_exists: bool` (lab-owned), `unit_of_work`.
- `CaseProposal`: `title`, `exercises: list[str]`, `measures`
  (`harness_claim|harness_development`), `task`, `observable`, `validation_hint`,
  `required_runtime_version`; lab-owned: `dropped_reason: str` (empty = kept).
- `DerivedCondition`: `label`, `role` (`control|treatment`), `overrides: dict` —
  exactly the `experimentcondition.overrides` vocabulary (`skills_disabled`,
  `skills_extra`, `harness_source`).
- Parsers `parse_component_map(raw) -> dict` and `parse_case_proposals(raw) -> dict`
  with the same tolerance `parse_findings` has today: non-JSON → `parse_error`, never
  invented rows; unknown `kind`/`needs`/`measures` values → row kept, value coerced
  to `unknown`, and a `warnings` list says which.

**1.2 Fixture.** `tests/harness_lab_app/fixtures/audit_talos_component_map.json` and
`audit_talos_cases.json`: hand-written model answers for talos (16 skills, `armed-gate.sh`,
`test-guard.sh`, `classify.sh` as `command`, `CLAUDE.md`/`AGENTS.md` as `rule`).
`tests/harness_lab_app/test_audit_contract.py`: both parse; a row with `needs: ["jira"]`
survives with the value; a row with `needs: ["telepathy"]` is kept with `unknown` and a
warning; prose instead of JSON is a `parse_error`.

**Exit gate:** `make test` green; the fixture is the spec the prompts must satisfy.

---

## Phase 2 — Evidence the auditor can reason from

`repository_audit_service.py`, `collect_evidence` and its helpers. All pure over
`(paths, read)` so `test_repository_audit.py` keeps testing against strings.

**2.1 Fuller sources.** `_SKILL_CHARS` 700 → 6000 (the body, not the frontmatter);
new `_HOOK_CHARS = 3000` and `hooks_evidence` returns script *contents* under
`hooks.scripts_text`; new `installers`: contents of any `*install*.sh`, `settings.template.json`,
`.claude/settings*.json` found in the tree; new `architecture_doc`: the first
`docs/ARCHITECTURE.md | ARCHITECTURE.md | docs/architecture.md` present, capped at
`_ARCH_CHARS = 6000`. Total evidence size is logged as a `RunEvent` so cost is visible.

**2.2 Lab catalogue.** `lab_skill_catalogue() -> list[{"name","description"}]` read from
`.github/skills/*.md` frontmatter (the governed source of truth per `CLAUDE.md`) plus
`.github/skills-external/`. Enters the evidence as `lab_harness.skills`; the model may
only name counterparts from it.

**2.3 Health at the pin.** `gate_check_service.build_health_spec(target)`: the existing
`build_gate_check_spec` minus the `GateCheckError` when there are no validation
commands (health with no commands is `{"status": "unmeasured"}`, in the spirit of
ADR-0048). `tasks.run_repository_audit` runs it through the hardened validation lane
before the first model call and stores `health_at_pin = {"status": "pass|fail|unmeasured",
"checks": [...], "counts": {"passed": n, "failed": m} | null}` in the evidence. Counts are
parsed from the tail with a tolerant regex (`(\d+) passed, (\d+) failed`); absent → null,
never guessed.

**2.4 Needs capabilities.** `runner_capabilities.service_capabilities()` → dict over the
`NEEDS_VOCABULARY`: `docker` from the executor kind, `network` true, `jira`/`github`/
`browser` from settings (`JIRA_*`, `GITHUB_TOKEN`, a `BROWSER_AVAILABLE` flag; default
false), `target_repo` false until a target-project mechanism exists. Declared, not
probed, and the row says `source: "declared"`.

**Tests:** `test_repository_audit.py` — a skill body past 700 chars is present in the
evidence; a hook's text is present; the installer is found; `lab_harness.skills` lists
`verify_changes`; `test_gate_check.py` — health with no commands is `unmeasured`, not an
error; counts parse from a talos-shaped tail and are null for an unfamiliar one.

**Exit gate:** `make check` green; `gather_evidence(talos-4)` (the existing real-checkout
test) shows the five new keys.

---

## Phase 3 — Two passes, and the lab's verdicts between them

**3.1 Prompts.** Replace `REPOSITORY_AUDIT_SYSTEM_PROMPT` with two:
- `COMPONENT_MAP_PROMPT`: keeps `summary`, `harness_verdict`, `language_notes`,
  `standards`, `gaps`; replaces `harness_components` + `use_cases` with `component_map`
  and `shape`. One row per component in the evidence, `claim` quoting the source,
  `lab_counterpart` only from `lab_harness.skills`, `needs` only from the vocabulary.
  Explicit: *do not say whether the lab can evaluate it; say what it needs.*
- `CASE_PROPOSALS_PROMPT`: receives the annotated map (with `evaluable_here`) and
  `health_at_pin`; proposes 3–6 cases, each with `exercises` from the map, `measures`,
  and a `validation_hint` under the same hint rules as today. Explicit: a case may not
  make a red suite its pass condition unless its task is to fix it; prefer
  `harness_claim` cases; a `harness_development` case must say so in `title`.

**3.2 Lab verdicts, `audit_contract.annotate_map(map, evidence, capabilities)`:**
`evaluable_here = all(needs satisfied)`, `evaluable_reason` names the missing need;
`counterpart_valid` against the catalogue (invalid → cleared, warning kept);
`shape.install_entry_exists` against `paths`. `annotate_cases(cases, map)`: drop with
reason when `exercises` is empty, names an unknown component, or names only
non-evaluable ones; keep `classify_case` on the hint for runnable/static/blocked as
today.

**3.3 Derived conditions, `audit_contract.derive_conditions(case, map)`:**
skills among `exercises` → `native-on {}`, `native-off {skills_disabled: [...]}`,
`lab-counterpart {skills_disabled: [...], skills_extra: [counterparts]}` (only when at
least one valid counterpart); no skills among `exercises` → the pair
`native {harness_source: native}` / `injected {harness_source: injected}` and
`conditions_note` saying hooks/rules are not toggleable.

**3.4 Task.** `tasks.run_repository_audit`: health → pass 1 → annotate → pass 2 →
annotate cases → derive conditions → `plan_cases` (unchanged) → store. Both calls
carry `trace_kind="repository_audit"` with `pass: 1|2` in metadata; planner tier via
`llm_transport.default_audit_model()` (already role-based). `findings_json` gains
`component_map`, `shape`, `cases`; `use_cases` stays absent for new audits.

**Tests:** `test_repository_audit.py` — with the Phase 1 fixture as the injected
gateway: `talos-ticket` is not evaluable (`jira`), `talos-plan` is; a counterpart not
in the catalogue is cleared with a warning; a case exercising only `talos-pr` is
dropped with reason; `talos-plan` yields three conditions and `native-off` carries
`skills_disabled: ["talos-plan"]`; a case exercising `armed-gate.sh` alone yields the
native/injected pair; `install_entry` `scripts/talos-install.sh` exists; a red
`health_at_pin` reaches pass 2's prompt text. Old fixture audits (with `use_cases`)
still parse and render.

**Exit gate:** `make check`; a real audit of talos-4 (`POST /api/targets/talos-4/
repository-audit`) stored with all three new keys and no `parse_error`.

---

## Phase 4 — Reading it

**4.1 Markdown.** `render_markdown` sections, in order: Resumen · Forma del harness
(shape, unit of work, install entry, salud al pin) · Mapa de componentes (table:
componente · afirmación · observable · contraparte · necesita · evaluable) · Casos
propuestos (each: qué mide, componentes, brazos derivados, validación, estado runner)
· Brechas · Veredicto del laboratorio. Every component row ends with its
`source_path`.

**4.2 API/frontend.** `frontend/src/types.ts`: `RepositoryAuditComponent`,
`HarnessShape`, `RepositoryAuditCase`, `DerivedCondition`; `findings_json` union keeps
`use_cases?` for old rows. `Audit.tsx`: component table with an evaluable badge and
the missing need on hover; case cards showing `measures`, `exercises` chips and the
derived arms as read-only pills; "crear caso" keeps working from a proposal
(`validation_command`/`validation_note` split unchanged). Old audits render the old
list.

**4.3 Docs.** `docs/guide-repository-audit.md` (or the existing wizard guide): the
five questions, the vocabulary of `needs`, what "evaluable here" means, and how a
proposal's arms map to `skills_disabled`/`skills_extra`. `memory/learnings.md`: the
talos-4 lesson in two lines.

**Exit gate:** `make check` and `make check-sync`; frontend typechecks; a screenshot
of talos-4's audit page in the PR.

---

## Phase 5 — Prove it on talos (no code)

Run the audit on talos-4; compare against the ADR's expected report; run **one**
proposed `harness_claim` case (ticket in prose → `SPEC.md` + `plan.json`) with the
derived `native-on` vs `lab-counterpart` arms, 2 reps, Haiku. The experiment audit's
`skills_invoked` against the case's `exercises` is the number that says whether the
map was any good. Record the outcome as an addendum to ADR-0058 and, if the map was
weak, open the deferred "auditor with tools" question with evidence instead of taste.

## Order and sizing

1 → 2 → 3 → 4 are sequential (each consumes the previous contract); 2.1–2.4 are
independent of each other and can be delegated in parallel; 4.1–4.3 likewise. Each
phase is one PR-sized unit and leaves `make check` green on its own.

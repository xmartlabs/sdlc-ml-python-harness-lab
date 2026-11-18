# Prompt: harness levels (minimal / full) for sdlc-ml-python-template

> **Status (2026-11-18): applied** in a local checkout of
> `sdlc-ml-python-template`, uncommitted, pending the maintainer's review and
> release (2.1.0). It became template ADR-0003. This prompt is kept as the
> specification the change was built against.

Paste the section below into an agent session opened at the root of
`git@github.com:xmartlabs/sdlc-ml-python-template.git` (v2.0.0 or later). It is
written against that repo's v2 engine (`catalog.py`, `overlay.py`, visibility and
per-skill `profile` metadata). It is not a copy of the lab's implementation,
whose engine predates v2.

Origin: harness lab ADR-0085 (profiles) and ADR-0086 (quality first), plus the
2026-11-18 usage review of three template-derived repos.

---

## Task

Add **harness levels** to this template. A project chooses one of two levels:
`minimal` or `full`. The skills outside the chosen level stay in the repo as
optional add-ons. They are not listed in any adapter file and not projected into
any native skills view, and a project can switch any of them on by name. Nothing
is deleted.

Read `.github/architecture.md`, `.github/standards.md`,
`.github/domain-boundaries.md`, ADR-0002 and `docs/skills-management.md` before
planning. Plan first, then implement in small verified steps.

## Why

- Every skill an adapter lists costs context on every agent call.
- A usage review of three repos built from this template (2026-11-18, Claude
  Code transcripts and git history) found only these skills invoked:
  - `plan_and_execute_feature` (11 sessions)
  - `systematic_debugging` (5)
  - `verify_changes` (3)
  - `research_current_info` (2)
  - `requesting-code-review`
  - `brainstorm_quick`
- 16 of 25 skills were never invoked.
- With `minimal`, the generated region of every adapter drops from about 5.1 KB
  to about 3.4 KB (−34%).
- Quality comes first. The harness exists to add on top of the model, so
  `minimal` is about what to carry **by default**, not a claim that the rest is
  useless. `full` stays one command away.

## Naming: do not overload `profile`

`SkillMeta.profile` already means a per-skill grouping label (`core`,
`python-ml`, `frontend`, `company-context`, `legacy`). Do **not** reuse that
word for the new concept. Use **harness level**:

- Definitions: `[harness_levels.<name>]` in `adapters/registry.toml`, or a new
  governed `.github/harness-levels.toml` if you judge the registry the wrong
  owner (justify it in the ADR).
- Project selection: `harness.toml` at the repo root, owned by the project and
  never written by a template sync:

  ```toml
  [skills]
  level = "minimal"          # or "full"
  include = []               # optional skills to switch on
  exclude = []               # level skills to switch off
  ```

## Required behavior

1. **Two levels:**
   - `full` is built in and means every live skill, exactly today's behavior.
   - `minimal` is declared with these seven skills:
     `plan_and_execute_feature`, `systematic_debugging`, `verify_changes`,
     `research_current_info`, `requesting-code-review`, `writing-plans`,
     `brainstorm_quick`.
   - Keep the definition format able to declare more levels later (`extends`
     is optional), but ship only these two.
2. **No `harness.toml` means `full`.** Existing projects and CI see zero
   change.
3. **Filter, never delete.** `.github/skills/` and `.github/skills-external/`
   stay complete. The selection applies wherever skills are projected:
   - the native views (`.claude/skills`, `.codex/…`, `.opencode/…`,
     `.agents/…`), via `link_tool` and the copy-tool manifests;
   - every adapter region (`render_region`, `render_tool`, `expected_file`);
   - the generated catalog doc. Do not drop skills from the catalog doc: mark
     each one as carried or optional for this project.

   Apply it at one seam, e.g. a `selected_skills(root, registry)` that wraps
   `_skills()` in `cli.py`, so link, render and check cannot disagree.
4. **Drift check.** `check_drift` must flag:
   - a native view whose set of skill entries differs from the selection;
   - adapters rendered for a different selection.

   `stale_native_entries` is the natural place for the first.
5. **No dangling routing.** `routing_rules`, `[[intent]]` and `small_model_note`
   name skills by backtick. Today the rules name `brainstorming`,
   `test-driven-development`, `generate_migration_plan`,
   `create_domain_contract`, `retrospective` and `bootstrap_project`, none of
   which are in `minimal`. A rendered adapter must never tell the agent to use a
   skill this project does not carry. Choose and document one approach:
   - (a) render only the rules and intents whose skills are all carried;
   - (b) give each rule a variant per level;
   - (c) render the missing names as "optional, not enabled here (add to
     `harness.toml` include)".

   Extend `tests/routing` so every backticked name in a rendered adapter is a
   carried skill or explicitly marked optional.
   The same applies to hand-written text that ships to projects: the
   `CLAUDE.md` / `AGENTS.md` operating playbook and `.claude/commands/*.md`
   (`/plan` names `brainstorming`, `/retro` names `retrospective`,
   `/orchestrate` names `subagent-driven-development`). Word them so they hold
   at both levels: name the minimal skill first, and mark the full-only skill
   as "if enabled".
6. **Agents stay coherent.** `.github/agents/*.md` bind skills
   (`skills: [...]`), and some bound skills are outside `minimal`
   (`generate_implementation_docs`, `create_domain_contract`,
   `refactor_to_clean_architecture`, `generate_migration_plan`,
   `validate_module_structure`, `generate_e2e_tests`). Pick and document a rule:
   - a projected agent pulls its bound skills into the selection; or
   - the agent is projected with a note that the skill is optional here; or
   - `minimal` does not project those agents.

   Do not leave an agent pointing at a skill that is absent.
7. **CLI and Make:**
   - `python -m ml_python_base.skills_sync level` lists the levels and what this
     repo carries.
   - `level --set NAME` writes `harness.toml` and relinks and re-renders.
   - Make targets: `make skill-level` and `make skill-level SET=full`.
   - `make sync-skills` must honor `harness.toml`.
   - Validate unknown level names, unknown skills in `include`/`exclude`, and a
     cycle in `extends`. Fail with an actionable message.
8. **Bootstrap:**
   - `make init NAME=x` defaults to `minimal`.
   - `make init NAME=x LEVEL=full` selects full.
   - `scripts/init_project.py` gets `--level`. It validates the level **before**
     any rewrite and never overwrites an existing `harness.toml`.
   - Update `bootstrap_project` and `docs/project-init.md`.
9. **Template sync:**
   - Level definitions are governance: add their file or registry section to
     `[template_sync].governance_paths` if it is a separate file.
   - `harness.toml` is project-owned: never listed and never written by sync.
   - The engine change is platform.
   - Decide whether this bumps `protocol` (currently 2) and say why in the ADR.
10. **Tests (pytest, under `tests/skills_sync`, `tests/init_project`,
    `tests/routing`):**
    - level resolution with `extends`, `include` and `exclude`;
    - unknown names;
    - no `harness.toml` behaves byte-identical to today;
    - `minimal` projects exactly 7 native entries and lists 7 skills in every
      adapter;
    - drift on a stale native view;
    - no dangling routing names;
    - init with `--level` and the never-overwrite rule.
11. **Docs:**
    - ADR-0003, "A project carries a harness level, not the whole catalog".
      Cover context (usage evidence above), the decision, the naming choice
      versus `SkillMeta.profile`, the routing and agent rules you chose, sync
      ownership, and consequences.
    - `docs/skills-management.md`, a "Harness levels" section.
    - `docs/skills-guide.md`, how to switch an optional skill on.
    - The CHANGELOG.
12. **Version:** this is a minor feature (2.1.0), unless you conclude the sync
    protocol bump makes it breaking. Explain the call.

## Constraints

- Follow this repo's governance: absolute imports, English-only code
  artifacts, clean architecture boundaries. Do not add runtime dependencies.
- Do not rename or delete any skill. Do not change any skill's `visibility`
  or `profile` metadata.
- Keep `full` (and the absence of `harness.toml`) byte-identical to today's
  generated output. Prove it with a test.
- Do not commit, push or create branches. Leave the changes for review.

## Done when

- `make check` and `make check-sync` pass.
- `make init NAME=demo --dry-run` (or the script equivalent) shows the level step.
- In a scratch copy, `make skill-level SET=minimal` leaves exactly 7 entries in
  each native view and 7 skills in each adapter region, with no dangling
  routing names.
- `make skill-level SET=full` restores today's output exactly.
- Report:
  - the per-adapter byte sizes for full and minimal;
  - the routing and agent rules you chose;
  - the protocol and version decision.

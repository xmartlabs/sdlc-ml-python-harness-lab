# ADR-0085: A project carries a skill profile, not the whole catalog

- **Status:** Accepted
- **Date:** 2026-11-17
- **Deciders:** Marcos Soto
- **Related:** [ADR-0011](0011-per-run-skill-ablation-and-staging.md),
  [ADR-0041](0041-a-skill-under-test-is-invoked-not-just-available.md),
  [ADR-0066](0066-harness-and-model-vary-together-only-in-a-full-grid.md),
  [ADR-0076](0076-per-step-reward.md)

## Context

Every run carried the whole governed catalog: 25 skills, about 5,000 characters of
descriptions listed in every adapter file, and close to 300 KB of skill bodies one
read away. The first skill census (2026-11-17) covered 18 runs measured per step,
across six models. Only three skills were ever opened: `plan_and_execute_feature`,
`verify_changes` and `writing-plans`. The other 22 were paid for on every run and
never used.

Before this ADR the lab had the tools to act on that, but not the path:

- `make prune` (ADR-0076) ablated one skill at a time. With 25 skills, several
  models and repetitions, that is hundreds of runs. It also cannot see redundancy:
  if two skills overlap, removing either alone changes nothing.
- The corpus credit ranked skills over all runs pooled. A skill that one model never
  opens and another relies on looked the same as a skill nobody uses.
- Nothing let a project, or a model tier, carry less than everything. The template
  bootstrapped every new project with the full catalog.

## Decision

1. **The census is sliced.** `StepRewardService.census(by=model|case|harness)`,
   `GET /api/scores/skill-census` and `make skill-census` report usage per group,
   beside each skill's size: description characters (paid by every run) and body
   bytes (paid when the skill is opened). It is still co-occurrence. It orders what
   to ablate and never removes anything.
2. **Skills can be ablated as a group.** A treatment that removes several skills is
   one pruning item, `skills:<a>,<b>`, judged by the same non-inferiority rule as a
   single skill. `make prune GROUP=1` removes the items in one arm. Without `ITEMS`,
   it takes every skill no measured run ever opened. A removable group goes as a
   whole. A group that is not removable is split and retried one skill at a time.
3. **A profile is a named subset.** `.github/skill-profiles.toml` defines the
   profiles (`minimal`; `extends` composes them; `full` is built in; a `core` middle level was dropped on 2026-11-18 — two levels, minimal and full). It is a
   governed path, so it travels with template syncs alongside the skills it names.
   `harness.toml` at the repository root selects a profile, plus `include` and
   `exclude` lists. It belongs to the project, and no sync writes it. Without
   `harness.toml` the project carries `full`, which is what every repository did
   before this ADR.
4. **A profile filters, it never deletes.** `link`, `render` and `check` build from
   the selection. `.github/skills*/` stays complete. Deleting skills at bootstrap
   would fight the template sync, which would bring them back. Filtering also keeps
   re-enabling a skill to one line, and a governed agent bound to a filtered skill
   still finds its file. `check-sync` now compares each native view's membership
   with the selection, so a stale symlink view is drift.
5. **New projects start small.** `make init NAME=x [PROFILE=…]` writes `harness.toml`
   with `minimal` by default, before the `sync-skills` it already chains. The profile
   is validated before anything is renamed. A template without profile definitions
   keeps the full catalog.
6. **In the lab, a profile is an ablation.** `disabled_for(profile)` is the
   `skills_disabled` list that turns the full catalog into the profile. A profile
   arm therefore needs no new run field and no new condition-hash input. `make
   skill-profile-experiment` builds a profile × model grid in mode
   `harness_by_model` (ADR-0066). The lab repository has no `harness.toml` and keeps
   `full` as its baseline.
7. **Survivors are refined by swap.** `make refine-skill` A/Bs a staged rewrite,
   under its own name, against the governed skill. The treatment disables the
   original and stages the candidate, and both arms carry the same base profile.
   Promotion stays manual.

## Consequences

- The profile contents shipped today are **provisional**: a judgement from 18 runs,
  where no claude run opened most skills at all. They are to be replaced with group
  pruning and profile-grid verdicts per model tier. The file says so.
- Profiles are per project, not per model, at runtime. A project picks one profile.
  A per-tier choice comes from the grid's verdicts, and today it is applied by hand
  in `harness.toml`.
- `skills_sync` and `init_project.py` are template platform code. The template gets
  this behaviour only when these changes are carried upstream (a platform PR). A
  governance-only sync carries just the profile definitions.
- The opencode lane still observes skill use only through `SKILL.md` reads, so a
  skill used under opencode can look unused. Read the census per harness
  (`BY=harness`) before trusting a zero.

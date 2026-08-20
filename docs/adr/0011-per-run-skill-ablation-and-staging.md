# ADR-0011 — Per-run skill ablation and staged-skill trials

- Status: accepted (points 4 and the staged-skill hash extended by ADR-0017)
- Date: 2026-07-08
- Relates to: ADR-0003 (condition hash), ADR-0005 (attribution), Pillar 4 (ablation)

> **Extended by [ADR-0017](0017-multi-file-staged-skills-and-remote-import.md).**
> A staged skill is now a whole tree, not one `SKILL.md`: the DB holds every file
> and `data/skills_staging/` is a rebuildable materialization. The staged-skill
> digest changed from `hash_file(SKILL.md)` to `folder_hash(dir)`, which re-bins
> every staged-skill arm recorded before that change — `metadata.json` carries
> `skills_extra_hash_algo` to keep the two generations legible. The hash rule in
> point 3 below is unchanged.

## Context

The lab's scientific goal is to treat the engineering harness (skills + docs +
hooks + agents) as the independent variable. That requires *ablation*: run the
same case/model with one harness factor toggled and compare distributions. But
the subject agent inherits the whole repo tree (the runner bind-mounts the repo
rw, ADR-0002), and the skill views it reads (`.opencode/skills/`, the generated
skills block in `OPENCODE.md`, `.claude/skills/`, ...) are artifacts of the
skills_sync engine with no per-run selection mechanism. There was also no way
to trial a *new* skill without editing the governed catalog directly.

## Decision

1. **Per-run ablation via the skills_sync engine.** `RunConfig` gains
   `skills_disabled` (and `skills_extra`); the runner adapter passes them as
   repeated `--disable-skill` / `--extra-skill` flags. Before executing,
   `bench_run.py` regenerates every tool's native skill view AND adapter-file
   skills block from the filtered/extended skill list (`link_tool` +
   `render_tool(skills=...)`), and restores the full views after the run.
   The governed source (`.github/skills*`) is never touched.
2. **Crash safety by regeneration, not locking.** Native views are generated
   artifacts, so restore is idempotent: every run re-syncs the full views at
   start (healing a previous crash), an `atexit` hook restores on every exit
   path, and worker concurrency 1 (ADR-0002) rules out interleaving. Residual
   risk: a SIGKILL leaves the tree ablated until the next run or a manual
   `make sync-skills`.
3. **Hash rule — ablation fields enter `condition_hash` only when non-empty.**
   `sorted(skills_disabled)` and `{staged_name: content_hash}` are added to the
   identity dict only if non-empty, so every historical/default-arm hash is
   preserved while any ablation (or staged-skill edit) flips the arm. Do not
   "simplify" this into always-present keys: it would silently re-bin every
   existing arm. During ablation `OPENCODE.md` is excluded from the
   `harness_git_dirty` check (the mutation is controlled and recorded as
   `skills_ablation: true`).
4. **Staged skills live outside the governed catalog.** A `StagedSkill` row
   (UI source of truth) plus `data/skills_staging/<name>/SKILL.md` (git-ignored
   materialization the runner injects). Promotion writes
   `.github/skills-external/<name>/SKILL.md` and runs the sync engine — it
   never commits; reviewing and committing is a human step.

## Consequences

- A/B validation of a skill is now a first-class flow: baseline run vs run
  with the skill disabled (or a staged skill enabled), grouped by
  `condition_hash`, compared on Validation + Attribution + Audit verdicts.
- The runner mutates the bind-mounted host repo during a run (generated
  artifacts only). Running `make sync-skills` or editing skills mid-run on the
  same machine will interfere; acceptable for a local single-user lab and
  documented in `docs/guide-skill-validation.md`.
- `skills_bundle` in `metadata.json` reflects the *effective* (post-ablation)
  set because exclusions are applied before metadata is written; attribution's
  `skills_available` stays correct with no changes.

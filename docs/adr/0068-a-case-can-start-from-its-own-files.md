# ADR-0068: A case can start from its own files

- **Status:** Accepted
- **Date:** 2026-10-21
- **Deciders:** Marcos Soto
- **Related:** [ADR-0050](0050-a-case-ships-its-own-tests.md),
  [ADR-0057](0057-a-case-is-validated-before-it-can-measure-anything.md),
  [ADR-0067](0067-a-frontend-case-is-graded-in-a-real-browser.md)

## Context

A bug-fix case needs the broken code to exist before the agent runs. A workspace
could start only three ways: empty (build from scratch), cloned from a git target,
or copied out of a SWE-bench Pro image. A small, self-contained "here is a page that
mostly works, fix it" case — the shape most day-to-day frontend work takes — had no
way in, so ADR-0067 deferred it.

## Decision

**A case's `task.yaml` may name a `starter` directory under `benchmarks/`; the
runner copies it into an empty workspace before the agent runs.**

- The path travels `task.yaml` → `Case.metadata_json.starter_dir` → `case_spec.json`
  `starter_dir` → `bench_run.seed_workspace`. Re-seeding the corpus refreshes it, like
  the validation commands.
- The runner refuses any path that does not resolve inside `benchmarks/`
  (absolute paths, `..`, other repository folders): the spec is a file, and a path
  outside the corpus would copy host files into a run.
- Symlinks are skipped, never followed. Only regular files are copied.
- A workspace that already holds files — a resumed or continued run — is left alone.
- Seeding and repository provisioning are exclusive: a case that clones a repository
  does not seed.

`frontend-bugfix-v1` is the first such case: a clinic-schedule page with four bugs of
four kinds (interaction, state, accessibility, injection) and ten browser tests, two of
them regressions.

## Consequences

- Bug-fix, refactor and "finish this half-built feature" cases are now cheap to write.
- **Regression tests are what make a bug-fix case honest.** The broken starter passes
  exactly the regressions and an empty workspace passes none, so a run cannot score by
  leaving the files alone or by deleting them. Every starter case should ship at least
  one test of behaviour that already works.
- Calibrated outside the runner and inside the runner's browser layer with no network:
  empty 0/10, starter 2/10, reference fix 10/10.

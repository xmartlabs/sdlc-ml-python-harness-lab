---
name: verify_changes
description: Use before considering work done — run the read-only quality gate and tests, interpret failures, and confirm the change is correct. The verification step of the working loop.
---

# Skill: verify_changes

## Purpose

Confirm that a change is correct and meets the repository's quality bar before
handing it off, using the existing read-only gates. Verification observes; it does
not mutate the tree.

## Required Input

- The change to verify (working tree, branch, or specific files).
- Any feature-specific behavior that should be exercised.

## Output Format

- The commands run and their results.
- A pass/fail verdict per gate (format, lint, types, security, tests, sync).
- For failures: the failing command, the relevant output, and the smallest fix.
- Relevant manual checks performed (e.g. a CLI/app run, behavior exercised).
- Known remaining risks.
- Whether the implementation is ready for review.

## Execution Rules

1. Run `make check` (ruff format check, ruff lint, bandit, mypy, pytest + coverage).
2. If skills, agents, or adapter files changed, also run `make check-sync`.
3. Do not mutate files during verification. Apply fixes deliberately with `make fix`
   / `make format`, then re-verify.
4. Exercise the actual behavior when feasible (focused `pytest`, a CLI run, or the
   `generate_e2e_tests` skill for critical flows).
5. Report honestly: if a gate fails or a step was skipped, say so.
6. Comply with `.github/automation.md` (CI is read-only; fix locally).

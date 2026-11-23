---
name: bootstrap_project
description: Use when starting a new project from a fresh clone of this template — guides the rename via `make init`, environment setup, template-remote configuration, and the first green quality gate, in any supported AI tool.
---

# Skill: bootstrap_project

## Purpose

Turn a fresh clone of this template into a named, working project with one
governed flow. Works identically from Claude Code, OpenCode, Codex,
Antigravity, or Copilot: the heavy lifting is system-enforced by
`make init` and the read-only gates, not by model behavior.

## Required Input

- New package name (lowercase snake_case, e.g. `churn_predictor`). Ask for it
  if not provided; do not invent one.
- Optional: whether the project tracks the upstream template
  (`TEMPLATE_REPO` override for `make template-remote-setup`).

## Execution Rules

1. Confirm the clone is uninitialized: `src/ml_python_base/` must still exist
   and the git tree must be clean. If the package was already renamed, stop —
   this skill is post-clone only.
2. Preview first when the user wants to inspect the change:
   `python3 scripts/init_project.py --name <name> --dry-run`.
3. Run `make init NAME=<name>`. It renames the package, rewrites references
   (preserving the upstream template URL needed by `docs/template-sync.md`),
   creates `.env` from `.env.example`, then chains `make install`,
   `make sync-skills`, `make template-remote-setup`, and `make ci`.
4. If the user tracks a different template fork, pass it through:
   `make init NAME=<name> TEMPLATE_REPO=git@github.com:org/fork.git`.
5. Ask the user to review `.env` (model endpoints/keys are machine-specific;
   defaults point at localhost). Never commit `.env`.
6. Verify the exit gate: `make ci` must be green before declaring success.
   If it is red, fix locally with `make fix` / `make format` — never weaken
   the checks.
7. Have the user review the diff, then commit the initialization as a single
   commit.

## Output Format

- The executed commands and their outcomes (including the `make ci` result).
- Anything that needs the user's hand: `.env` values, template fork URL,
  GitHub remote for the new project.
- Suggested next step: ideate with `brainstorm_quick` (scoped work) or the
  external `brainstorming` skill (design-gated work), then
  `plan_and_execute_feature`.

## Governance References (Mandatory)

- `.github/architecture.md`
- `.github/standards.md`
- `.github/domain-boundaries.md`
- `docs/project-init.md` (mechanism details and caveats)
- `docs/template-sync.md` (staying in sync with the upstream template)

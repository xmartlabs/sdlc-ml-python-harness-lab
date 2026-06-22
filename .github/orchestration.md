# Orchestration Policy

This file defines Level 4 orchestration so complex tasks follow deterministic execution flow.

## Plan-First Requirement

- For non-trivial engineering tasks, create an explicit plan before writing code.
- The plan must include ordered steps, expected outputs, and validation checkpoints.

## Step-by-Step Execution

- Execute work in explicit phases.
- Complete each phase before moving to the next one.
- Re-scope only when a blocking constraint is discovered.
- Size each step to the executor model tier; for weak/self-hosted executors,
  prefer one-file, independently-verifiable steps and a runnable milestone first.
  See `docs/task-sizing.md`.

## Mandatory Diff Review

- Review generated diffs before finalizing.
- Check for unrelated file churn and architectural boundary violations.
- Confirm naming, comments, and docs remain in English.

## Automation Validation

- Validate results against `.github/automation.md` requirements.
- Prefer project command sequence for quality gates:
  - `make format`
  - `make fix`
  - `make lint`
  - `make test`

## Skill Invocation Rule

- Do not perform direct large-scale generation when an internal skill applies.
- Select and invoke relevant skill(s) from `.github/skills/` first.
- Use external synced skills only when no internal skill covers the capability.

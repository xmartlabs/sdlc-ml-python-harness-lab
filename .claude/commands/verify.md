---
description: Run the read-only quality gate and tests, then summarize results.
allowed-tools: Bash(make check:*), Bash(make test:*), Bash(make check-sync:*), Bash(pytest:*), Read
---

Verify the current state of the working tree.

Steps:

1. Run `make check` (ruff format check, ruff lint, bandit, mypy, pytest with coverage).
2. If skills, agents, or adapter files changed, also run `make check-sync`.
3. If anything fails, report the failing command and the relevant output, then
   propose the smallest fix. Do not mutate files as part of verification — use
   `make fix` / `make format` separately and deliberately.
4. On success, give a one-line green summary (what passed and coverage if shown).

This is the verification gate referenced in the operating playbook. Run it after
substantive changes and before considering work done.

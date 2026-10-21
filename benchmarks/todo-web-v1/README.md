# todo-web-v1

The lab's first frontend case (ADR-0067): build an accessible, offline todo app as one
static page, graded in **headless Chromium** by a shipped Playwright suite. The tests
find elements the way a user or a screen reader does — roles, accessible names,
`data-testid` hooks and the keyboard — so they grade behaviour, not code style.

What it can measure that the Python cases cannot:

- **Frontend quality the eye misses** — keyboard order, `aria-pressed` state,
  persistence, and HTML escaping (test 12 is an injection attempt).
- **Capability expansion** — whether a harness lets an agent (or the developer behind
  it) ship sound frontend work outside their main stack.

The first validation command is a tamper guard: the tests must be byte-identical to the
checked-in ones. The second confirms the entry point exists. Each of the twelve tests is
then its own command, so `test_pass_rate` has twelve steps of resolution.

## Requirements on the runner

The runner image must provide Python Playwright, `pytest-playwright` and Chromium
(`Dockerfile.runner`), and `.env` should set `BROWSER_AVAILABLE=1` so the audit's
capability table reports it. Without them every test fails with an import or launch
error, which reads as an environment failure rather than an agent failure.

```bash
make prompts                                     # regenerate prompt_harness.md
make case-import TASK=benchmarks/todo-web-v1/task.yaml
```

## Calibration (2026-10-21, outside the runner)

| Workspace | Result |
|---|---|
| empty | 0 / 12 — the case cannot pass on an untouched tree (ADR-0057) |
| a plausible weaker build (no persistence, `innerHTML`, no `aria-pressed`) | 9 / 12 |
| reference implementation (kept outside the repository) | 12 / 12 |

The reference is deliberately not checked in: a solution in the repository is a
solution the agent can read.

# frontend-bugfix-v1

A clinic-schedule page that "mostly works", seeded into the workspace from
`starter/` before the agent runs (ADR-0068). Four bugs of four kinds:

| Bug | Kind | Root cause |
|---|---|---|
| The doctor filter shows nothing | interaction | `<select>` values are strings, doctor ids are numbers; `===` never matches |
| The counter disagrees with the list | state | the count is computed from all appointments before filtering |
| Cancel is unusable without a mouse | accessibility | a clickable `<div>` — no role, no focus, no keyboard |
| A patient name runs as HTML | security | the row is built with `innerHTML` from data |

Graded in headless Chromium by ten Playwright tests, two of them regressions. The
regressions are the point of a bug-fix case: the broken starter passes exactly those
two, an empty workspace passes none, so a run cannot score by leaving the page alone
or by deleting it.

## Calibration (2026-10-21)

| Workspace | Result | Where |
|---|---|---|
| empty | 0 / 10 | host |
| broken starter (untouched) | 2 / 10 — the regressions | host and runner layer, no network |
| reference fix (kept outside the repository) | 10 / 10 | host and runner layer, no network |

## Running it

Needs the browser layer of `Dockerfile.runner` (see `todo-web-v1`).

```bash
make prompts
make case-import TASK=benchmarks/frontend-bugfix-v1/task.yaml
```

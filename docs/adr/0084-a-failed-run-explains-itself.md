# ADR-0084: A failed run explains itself, and the stall clock outlasts the harness's own

- **Status:** Accepted
- **Date:** 2026-11-16
- **Deciders:** Marcos Soto
- **Related:** [ADR-0022](0022-watchdog-splits-model-wait-from-agent-stall.md),
  [ADR-0026](0026-run-continuation-as-child-runs.md),
  [ADR-0083](0083-a-stopped-run-retries-from-scratch-or-from-its-last-step.md)

## Context

`racing3d-v1-K-004-C-001` was a continuation asking a racing game to take a
kart-racer style. It was stopped after 55 minutes with "Run stalled: … The agent
is blocked in a tool call that will never return". The operator could not tell
from the run page whether to retry, whether the prompt had been too large for
three steps, or whether the plan's quota had run out.

The answer was in `steps/step-1.events.jsonl` (4 MB of stream-json), and it
contradicted the verdict:

- Every test had passed. A full three-lap playtest, at 16 fps in the runner,
  took eight minutes.
- The agent waited on a second playtest. Claude Code's Bash tool allows one call
  ten minutes, then moves it to the background and answers. The lab's stall
  clock (`run_stall_seconds`) was also ten minutes. The two fired in the same
  second, and the lab stopped a healthy run.
- The only usage-limit event said `allowed`. No API errors.

So there were two problems. The watchdog's clock could undercut the harness's
own, and the evidence that explains a failure was not where the operator looks.

## Decision

1. **The stall clock never undercuts the harness's command timeout.** A harness
   may declare the longest one command can run before the harness answers on its
   own (`HarnessSignals.command_timeout_seconds`). For `claude` that is 600 s.
   The budget for tool-call silence is `max(run_stall_seconds, that + 300 s)`
   (`run_failures.stall_budget`). Zero still disables it. The cap is pinned in
   the run environment (`BASH_MAX_TIMEOUT_MS=600000`), so the CLI's default
   cannot drift away from the floor. The stall verdict no longer claims the
   call "will never return". It says the agent was inside the command shown,
   which may be hung or long, and names `run_stall_seconds`.
2. **A failed run shows its evidence, with no model involved.**
   `GET /api/runs/{id}/failure-evidence` (`run_diagnosis.failure_evidence`)
   condenses the run's files into a summary of a few kilobytes:
   - the recent commands, each with how long it ran and the end of its output;
   - the command still open when the run stopped;
   - the usage-limit state, where one `rejected` outranks any `allowed`;
   - the API errors the harness reported;
   - which steps finished, and the task;
   - the log tail, for harnesses without an event stream.

   Every string passes through `redact`. The run page shows it as **Why it
   failed** on any failed or cancelled run.
3. **A model's reading is on demand, cached, and advice only.**
   `POST /api/runs/{id}/diagnose` sends that summary, never the raw log, through
   `llm_transport` to `DIAGNOSIS_MODEL` (default: `AUDITOR_MODEL`, then the
   transport's default). The model returns:
   - the cause, and the evidence for it;
   - whose issue it was: the prompt, the agent, the lab's configuration, or the
     provider;
   - whether a retry would work, and which mode;
   - what to do next, with a follow-up prompt when a continuation fits.

   The answer is stored in `data/runs/<id>/diagnosis.json`, keyed by the
   failure (end time and reason). Reopening the page is free, and a retried
   run that fails again is diagnosed afresh. With no transport the endpoint
   answers 503 and nothing is cached. It never touches a score, a verdict or
   the measurement.

## Consequences

- A claude run can sit quietly in one command for up to 15 minutes before it
  is called stalled, five more than before. A truly wedged command now costs
  those five extra minutes. In exchange, a long playtest is never cut by
  accident.
- `stalled` remains a result about the agent. Raising the floor only stops the
  lab from blaming the agent for a wait its own harness was built to allow.
- Every failed run is diagnosable from its page without reading JSONL. When an
  LLM is configured, one click costs one small call. The real failure above
  condenses to about 12 KB.
- The panel's facts come from claude's event stream. For opencode they come
  down to the step summary and the log tail, until that harness gets a parser.

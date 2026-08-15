# ADR-0022: Split the silence clock by what the run was waiting for

- **Status:** Accepted
- **Date:** 2026-08-13
- **Deciders:** Marcos Soto
- **Related:** [ADR-0020](0020-context-window-preflight-and-alias-merge.md),
  [ADR-0021](0021-benchmark-artifact-integrity.md),
  [local-model-runtime-config.md](../local-model-runtime-config.md)

## Context

The lab's success rate collapsed and the models looked responsible. A 35B that had
previously built Tetris now "did nothing"; nemotron-120B via the gateway, which had
solved the same case, now died on timeouts. Bypassing the gateway changed nothing.
Two defects in the lab explain all of it, and both are measurement failures rather
than model failures.

**A single silence clock killed healthy runs.** `run_stall_seconds` (600) was
introduced for run `K-034`, where the agent shelled into a pygame process that ignored
SIGTERM and held opencode's stdout pipe open for 47 minutes. That clock cannot tell
*why* a run is quiet. `K-050`'s last lines were `message=loop step=1` → `message=process`
→ `message=stream providerID=lmstudio modelID=qwen/qwen3.6-35b-a3b` →
`"llm runtime selected"`, then silence, then exit 137 exactly 600s later. No tool call
was in flight: the agent was waiting for the first token of its first turn. Measured
directly against that box, a trivial prompt has a TTFT of **0.2s** and the harness's
~24k-token prompt **318.2s**. The database agrees: the 35B runs `K-040/049/050` recorded
99/221/126 events with maximum gaps of 610/778/610s, while the 9B runs `K-046/047/048`
recorded 1727–3791 events with maximum gaps of 28–76s. The threshold sat below the 35B's
prefill time, so the 35B could never pass it and the 9B always could.

**Connectivity failures were recorded as model timeouts.** opencode retries a failing
stream forever. `K-029` logged **123** consecutive
`AI_APICallError: Cannot connect to API` errors between 21:13 and 22:23, retried every
~30s until the 7200s ceiling, and was filed as `Run timed out after 7200s` — which reads
as nemotron attempting the case and failing. The same error class appears against
lmstudio in `K-041`, and this pattern accounts for the block of eight 130–157 minute
"timeouts" from `K-018` to `K-030`.

**Both were counted as attempts.** `UNMEASURED_KINDS` contained only `"lab"`, and neither
failure produced a `lab`-prefixed reason, so both landed on the leaderboard as evidence
about the model.

## Decision

The watchdog will decide what a silence means from what the run was doing when it went
quiet, and only silences the agent itself caused will count as results.

1. **Two clocks, chosen per line.** `run_failures.awaits_model_response()` prefix-matches
   opencode's `message=stream ` / `llm runtime selected` against each streamed line. While
   a request is in flight the budget is `run_model_wait_seconds` (default **2400**);
   otherwise it is `run_stall_seconds` (unchanged, **600**). The trailing space in
   `"message=stream "` is load-bearing — `message="stream error"` is the opposite
   situation.
2. **Give up on a dead endpoint.** After `run_max_stream_errors` (default **5**)
   back-to-back `message="stream error"` lines with no progress between them, the run is
   stopped and the provider's own error text ships with the verdict. Only lines that are
   not retry setup (`message=stream`, `llm runtime selected`, `message=process`,
   `message=loop`) reset the counter — otherwise `K-029`'s retry loop would reset it
   forever.
3. **Three distinct verdicts, and two of them are unmeasured.** `Run stalled:` (the agent
   is stuck in a tool call — a real result), `No model response:` (the endpoint accepted
   and never answered) and `Model endpoint failing:` (the provider refused). The last two
   are classified `lab`, so `is_unmeasured()` keeps them off the leaderboard.
4. **The verdict names the knob.** `No model response:` states the current
   `run_model_wait_seconds` and points at `docs/local-model-runtime-config.md`, because
   only the operator can tell "too slow to be worth it" from "threshold set too low".

Defaults are chosen from the measurements above: 2400s is far above any prefill observed
here and far below the 7200s total budget, and a genuinely dead endpoint is caught by the
error count long before either.

## Consequences

**Positive**

- Slow local models are benchmarkable. A 35B is no longer killed for prefilling.
- The leaderboard stops absorbing lab failures as model attempts, in the two most common
  ways it was doing so.
- A run that cannot reach its provider ends in minutes with the provider's error attached,
  instead of burning two hours and reporting a timeout.
- The `K-034` protection is intact: a hung tool call still dies in 10 minutes and still
  counts as a result.

**Negative / trade-offs**

- The mechanism reads opencode's log format. A change to those markers degrades the split
  silently — the run falls back to the stall clock. The markers and the taxonomy live
  together in `run_failures.py` for that reason, and the tests assert against verbatim log
  lines rather than paraphrases.
- A model wedged *after* accepting a request now takes up to 40 minutes to be declared
  dead, against 10 before. That is the price of not killing healthy prefill.
- Historical runs keep their old, wrong classification; this is not retroactive.

## Alternatives considered

- **Just raise `run_stall_seconds` to 2400.** One line, and it un-breaks the 35B — but it
  also gives every hung tool call 40 minutes, which is the exact regression `K-034` was
  fixed for, and it still files the result as the agent's fault.
- **Probe the endpoint from the worker when a run goes quiet.** More direct, but it tests
  a different connection than the runner container's and says nothing about whether *this
  request* is progressing.
- **Trust the total timeout only.** Cheapest, and what produced the `K-018`…`K-030` block
  of two-hour timeouts that measured nothing.

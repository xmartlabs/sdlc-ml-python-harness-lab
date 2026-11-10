# ADR-0075: Every arm is measured by the same instrument

- **Status:** Accepted
- **Date:** 2026-11-09
- **Deciders:** Marcos Soto
- **Related:** [ADR-0034](0034-score-registry.md),
  [ADR-0046](0046-an-experiment-reports-validity-before-it-reports-a-verdict.md),
  [ADR-0049](0049-a-bare-arm-is-a-masked-runner-not-a-lab-root-run.md),
  [ADR-0073](0073-a-measurement-names-its-real-instrument-and-its-reach.md)

## Context

The experiment "does the full harness improve outcomes over the bare model" ran
`tetris-v2` five times per arm on `lmstudio/prism-ml/bonsai-27b` through opencode. Its
cost half could not be read:

- **The bare arm had no tokens and no minutes on any run.** `pull_usage_opencode_db`
  matched sessions with `directory = /app`. A masked bare runner starts opencode in
  `/app/workspace/<run>` (ADR-0049), so the query could never match.
- **The full-harness arm had them on 3 of 5 runs.** The query also required
  `title LIKE '%<run_id>%'`. opencode's `title` agent (the same model) writes that
  title, and bench_run never sets it. Whether a run was measured depended on whether
  the model happened to copy the run id into a title.
- **The figures that were recorded undercounted.** `time_minutes` summed the spans of
  the matched sessions only: 5.8 min for a 109-minute run, 9.1 for a 217-minute one.
- **The prompt cache never reached `billed_tokens`.** The reader stored it as
  `tokens_cache_read`, but `collect_cost` reads `cache_read_tokens`.
- **Self-hosted cost was "unknown".** opencode reports `0` for an unpriced model, and
  the reader dropped it on purpose, so a free run read as a missing figure.
- **The comparison computed deltas anyway.** A mean over 3 runs was set against an arm
  with none, and the page showed a token "difference" between harnesses.

The token data could not be recovered. opencode's store lived in each ephemeral
runner container, and `--print-logs` carries no per-request usage.

## Decision

1. **opencode usage is joined by session id.** `bench_run.opencode_session_ids` reads
   every `message=created id=ses_…` line from the run's own logs.
   `pull_usage_opencode_db` sums those sessions plus any child session whose
   `parent_id` points at one. Title and directory are never used. The same pass
   records turns (assistant messages), tool calls (`tool` parts) and `model_minutes`
   (the summed span of each assistant turn).
2. **Generated tokens include reasoning.** `output_tokens = output + reasoning`, with
   `reasoning_tokens` kept beside it. A new registry metric, `output_tokens`, is in
   the headline set: when the bill is $0, decode time is what a harness costs.
3. **One clock for every harness and arm.** `time_minutes` is the step clock: the wall
   time of every harness invocation, summed by bench_run (`time_source: step_clock`).
   It overrides any source's own duration. Provisioning, scoring and archiving stay
   outside it.
4. **A self-hosted run costs `0`, recorded as measured.** When the harness is opencode
   and every addressed model is `lmstudio/…`, `ollama/…` or a `gateway/lmstudio-…` or
   `gateway/ollama-…` alias, `cost_usd = 0` with `cost_source: self_hosted`. claude
   and codex are excluded because they drop a local id and use their own cloud
   default.
5. **A metric one arm lacks is not compared.** `Delta.comparable` is false when either
   arm misses the metric on any run. The difference is withheld, the headline omits
   it, and a `degraded` validity issue, `incomplete_cost_metrics`, names the cost
   figures (`cost_usd`, `billed_tokens`, `wall_time_minutes`) and the missing runs per
   arm.
6. **One assembly path.** `bench_run.assemble_usage` builds `usage.json` for both the
   managed and the interactive path. Before this change they merged sources in
   different orders.

## Consequences

- Both arms of a harness experiment on a local model now carry the same fields: input,
  generated and cached tokens, turns, tool calls, step-clock minutes and model
  minutes, and a $0 cost.
- `wall_time_minutes` changes meaning for opencode runs, from summed session spans to
  the step clock. The old values were partial, and every experiment that held them
  was deleted on 2026-11-09 at the operator's request. The registry version stays at
  `v1`.
- An experiment with gaps still shows its outcome comparison. Only the cost half is
  withheld, and the page says why.
- The opencode reader depends on the opencode 1.19.x schema (`session`, `message`,
  `part`). A schema change degrades to "unknown" (`None`), never to a wrong number,
  and the work counts fail independently of the token totals.

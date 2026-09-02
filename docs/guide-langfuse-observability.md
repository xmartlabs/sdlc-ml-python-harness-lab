# Inspecting a run in Langfuse

Every finished run leaves enough behind in `data/runs/<run_id>/` to reconstruct
what happened: the prompt it was given, the prompt each step was given, the log
each step produced, the diff it wrote, the tokens it burned, and the report it
signed off with. This integration turns that directory into a **Langfuse
session** so you can read a run turn by turn, compare two arms side by side, and
send someone a link instead of a log file.

It is **off until you turn it on**, and it stays off unless both API keys are
present. A lab without Langfuse opens no sockets and shows no buttons.

## Turning it on

Set these in `.env` (or in the web UI under **Settings → Tracing**):

| Variable | Default | What it does |
| --- | --- | --- |
| `LANGFUSE_ENABLED` | `false` | The master switch. Anything but `1/true/yes/on` means off. |
| `LANGFUSE_HOST` | `https://cloud.langfuse.com` | Cloud or your self-hosted instance. |
| `LANGFUSE_PUBLIC_KEY` | — | Required. Tracing stays off without it. |
| `LANGFUSE_SECRET_KEY` | — | Required. Tracing stays off without it. |
| `LANGFUSE_TRACING_ENVIRONMENT` | `lab-local` | Separates this lab from other senders in the same project. |
| `LANGFUSE_PROJECT_URL` | — | Your project's UI URL. Only used to build deep links. |
| `LANGFUSE_SESSION_PREFIX` | `bench-` | Session id is `<prefix><run_id>`. |
| `LANGFUSE_AUTO_EXPORT` | `true` | Export automatically when a run finishes. Set `false` to export by hand only. |

Then check the wiring:

```bash
make langfuse-check
```

It prints the resolved configuration and verifies the host and the credentials
without sending anything. The **Health** panel carries the same check as a
`Langfuse tracing` row; tracing switched off reads as `ok` there, because not
using Langfuse is a choice, not a fault.

## What a run looks like

One run is one **session**, id `bench-<run_id>` — the same id
`scripts/bench_run.py` has always written into `metadata.json`. Inside it:

- **`run:<run_id>`** — the envelope trace. Input is the case prompt, output is
  `report.md`, and it carries the run's tokens and cost, its state, exit code,
  score and objective validation. A failed run's envelope is marked `ERROR` with
  the failure reason, so you can find it by filtering on level alone.
- **`step-1` … `step-N`** — one trace per iteration. Input is the prompt that
  step was actually given (`steps/step-N-prompt.md`, or the case prompt for the
  first step), output is the step's outcome (the final assistant message when
  the artifacts record one, the log tail otherwise), and the span is timed from
  the step header to its last logged line. When the step's terminal facts are
  on disk (claude's `result` event), the step also carries its own token usage,
  cost, `stop_reason`, `ttft_ms` and turn count.
- **Inside each step** — the timeline the step's artifacts can reconstruct
  ([ADR-0039](adr/0039-step-traces-carry-per-call-detail.md)):
  - *generations* — one per LLM call. claude runs get one per assistant
    message (real model, per-turn input/cache tokens from the stream events);
    opencode runs get one per `stream`→`loop` window in the log (real
    wall-clock timestamps, `agent` label, gateway model). Each observation's
    `timing_source` metadata says whether its clock is real (`events`, `log`)
    or `interpolated` (older claude runs, whose events carry no stamps).
  - *tools* — claude lane only: one observation per `tool_use`/`tool_result`
    pair, with the tool's input and output. A call that never saw its result
    (the step died mid-call) is kept and flagged `unresolved` — it is often
    the crash site.
  - *log* — the full log tail, moved out of the step's output into its own
    observation so the step reads as an outcome instead of a wall of text.
- **`diff`**, **`audit`**, **`validation`** — attached to the envelope when the
  run produced them.

A single-prompt case and a ten-step case use the same shape: the short one just
has one step trace in its session.

Every trace is tagged from the run's metadata — `harness:opencode`,
`executor:qwen3-coder-30b`, `benchmark:tetris-v1`, `method:...`, `prompt:...` —
which is how you pull "every claude run on this benchmark" out of the project.

## Exporting by hand

Auto-export runs at the end of every run and again after validation. Everything
else is a command away:

```bash
make langfuse-export RUN=tetris-v1-K-061      # one run
make langfuse-export RUN=tetris-v1-K-061 DRY=1  # build it, print it, send nothing
make langfuse-backfill                        # every run on disk
make langfuse-backfill LIMIT=20               # the first 20
```

The run detail page has the same two actions: **Send traces** and **View in
Langfuse** (the link needs `LANGFUSE_PROJECT_URL`).

**Re-exporting is safe.** Trace and span ids are derived from the run id plus
the run's `condition_hash` (or `created_at` for pre-hash runs), so a second
export updates the same session rather than creating a duplicate — while two
*different* runs that happen to share a run id (a container-volume copy and the
local `data/` copy) can no longer overwrite each other. That is what makes
backfilling a hundred runs a repeatable operation.

`DRY=1` works even when tracing is switched off — it is the way to see the trace
shape before pointing this at a real project. Add `--json` to the script for the
full OTLP payload:

```bash
uv run python scripts/export_langfuse_traces.py --run tetris-v1-K-061 --dry-run --json
```

## What gets redacted

Prompts, logs and diffs are scanned before they leave the machine, and
credentials are replaced with `<redacted>`: API keys, GitHub and Slack tokens,
AWS keys, private key blocks, `Bearer`/`Basic` headers, and `KEY=value`
assignments that look like secrets. `GATEWAY_TOKEN=sk-live-…` becomes
`GATEWAY_TOKEN=<redacted>` — the key is kept so the line still reads.

Home paths and `localhost` addresses are **not** masked. They are what makes a
trace worth reading when you are debugging why a run could not reach the
gateway.

The `KEY=value` rule is deliberately blunt, so a source constant inside a diff
(`TETROMINO_KEYS = [...]`) can be masked too. Losing a line of a diff is the
right side of that trade.

## Limits

- **Traces appear when a run ends**, not while it runs. Watching a run live is
  the run detail page's job.
- **Per-call tokens exist only where the artifacts record them.** claude runs
  carry per-generation input/cache tokens; their per-generation `output_tokens`
  are streaming snapshots that undercount the finished message (marked
  `output_tokens_source: stream_snapshot` — do not sum them). opencode
  generations carry timing and model but no tokens. The exact text sent to the
  model (system prompt, full context) and exact per-call cost still need the
  gateway forwarding generations under the same `session_id`; that layer is
  not built yet.
- **Step timing is approximate for harnesses that log no clock.** opencode
  stamps every line; claude does not, so its step boundaries are inferred from
  the step header and file mtimes, clamped so a step never appears to outlive
  the next one. Inside a claude step, events are stamped with `_received_at`
  as they arrive from now on; runs recorded before that stamp existed have
  their per-call observations spread evenly across the step
  (`timing_source: interpolated`).
- **The session row is filed under the `default` environment.** Langfuse stamps
  `LANGFUSE_TRACING_ENVIRONMENT` on every trace and observation, but the session
  record it derives from them keeps `default`. Filtering the *Sessions* list by
  environment therefore hides the run; the *Traces* list filters correctly. Open
  a session from one of its traces, or from the run detail page's link.
- **A Langfuse outage is invisible to a run.** The exporter never raises; it
  writes a run event saying the export was skipped and the run finishes exactly
  as it would have.

## Related

- [ADR-0027](adr/0027-langfuse-traces-rebuilt-from-run-artifacts.md) — why
  traces are rebuilt from artifacts instead of emitted live.
- [ADR-0039](adr/0039-step-traces-carry-per-call-detail.md) — how the inside
  of a step becomes generations and tool calls, and what the timing labels
  promise.
- [plans/2026-08-langfuse-observability.md](plans/2026-08-langfuse-observability.md)
  — the full plan, including the not-yet-built gateway layer.

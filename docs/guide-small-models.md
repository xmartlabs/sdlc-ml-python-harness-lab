# Measuring small and self-hosted models

How to find out whether a model you serve yourself — through LM Studio, Ollama or the
LiteLLM gateway, driven by opencode — can *reliably* do real work, and whether a
harness raises what it can do. Background and the exact rule:
[ADR-0069](adr/0069-a-model-is-judged-by-the-tier-it-does-reliably.md).

## The question to ask

Not "did it pass?" but "**which tier of task does it do reliably, with which harness?**"
A model that solves a case once has shown it *can*. A team choosing it needs to know it
*does*, run after run.

| Reading | Means |
|---|---|
| **reliable** | ≥4 cases, every case passing in ≥4 of 5 runs, pooled 95% lower bound ≥70% |
| **capable** | it passed, but not often enough — or with too few runs — to rely on |
| **not yet** | at least one case below 4 of 5 |
| **too few cases** | fewer than 4 cases in the tier; *passing* when every case so far clears 4 of 5 |

The **frontier** is the highest tier reliable with every tier below it reliable too.
Three runs never show more than *capable*: 3 of 3 has a lower bound of 44%. While a tier
is short of cases the panel shows **Tier N?** — where the frontier would sit on the runs
so far — never a bare frontier.

## Before you run: make the model able to talk

Most losses of small local models in agent loops are not reasoning failures. They are
the model and harness failing to *talk*: a malformed tool call, an unknown tool, a
context window too small. Fix these first or you will measure your serving setup.

- **Context window.** The system prompt alone is ~14k tokens; an agentic run needs room
  for files and tool output. **Settings → Check context** measures what the host
  *actually* serves. Below 32k the lab refuses to launch; below 64k it warns. Ollama's
  default is 4k on small GPUs — set `num_ctx`; in LM Studio set the context length
  explicitly, because a just-in-time load uses the model's small default.
- **Tool calling.** Through LiteLLM use the `ollama_chat/` prefix and declare
  `supports_function_calling`, or tool calls degrade to JSON mode. Some models need a
  specific chat template or parser (gpt-oss: harmony; Qwen3.x: its own tool parser;
  Devstral: mistral).
- **Output limit.** Set opencode's `limit.output` per model; a reasoning model that runs
  out of output budget mid-answer looks like a failure.

When a run fails, the capability panel tells you to check the log: a *transport*
failure (tool schema error, context overflow, aborted stream) is a serving or harness
fix, not a limit of the model.

## Run the clinic ladder

The clinic ladder is three cases of rising difficulty on one realistic domain —
appointment scheduling — in pure Python, so any model can run it through any harness:

| Tier | Case | Hard part |
|---|---|---|
| 1 | `clinic-slots-t1` | half-open intervals, lunch, sorted output |
| 2 | `clinic-booking-t2` | no double-booking of doctor *or* patient, atomic reschedule, idempotency |
| 3 | `clinic-rules-t3` | DST, UTC storage, an injected clock, an audit trail with no patient data |

1. **Evaluate → Harness x model.** Pick the three clinic cases.
2. Pick your models — your self-hosted ones from the gateway list, plus a frontier model
   as a reference. Keep the partial rung on: the levels become *no harness → governance
   only → full harness*.
3. **5 repetitions.** Fewer cannot establish reliability.
4. Read the experiment page:
   - **Harness x model** — the harness effect per model, and whether it shrinks as the
     model grows;
   - **Capability frontier** — per arm, the highest tier done reliably.

A result worth sharing reads like: *"Qwen3.6-27B through opencode does tier 1 reliably
with no harness and reaches tier 2 with the full harness; tier 3 stays capable-only.
Opus 5.5 does tier 3 with or without it."*

## Cost of a frontier

A reliable tier needs at least 4 cases x 5 runs per arm. The clinic ladder ships one
case per tier, which is enough to *see* the frontier move and to calibrate a model, not
yet enough to *establish* it — adding three cases per tier is the next step. Self-hosted
runs cost compute time rather than tokens; the cost per resolved task in the harness x
model panel compares them with a hosted model on the same cases.

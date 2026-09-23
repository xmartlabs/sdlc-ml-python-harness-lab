# ADR-0063: The context limit opencode is told is read off the serving host

- **Status:** Accepted
- **Date:** 2026-09-22
- **Deciders:** Marcos Soto
- **Related:** [ADR-0012](0012-dynamic-gateway-model-list.md),
  [ADR-0022](0022-watchdog-splits-model-wait-from-agent-stall.md),
  [`docs/local-model-runtime-config.md`](../local-model-runtime-config.md),
  `memory/learnings.md` ("LM Studio JIT auto-load serves the model's *default* context")

## Context

Interactive sessions and lab runs against the LAN LM Studio box (`qwen3.6-35b-a3b`,
41k loaded) showed two latencies that did not fit: a first call of a minute on a
one-word prompt, and mid-session turns that waited for minutes each. The LM Studio
chat window on the same model answered in a second.

Measured on 2026-09-22 with a 35k-token prompt and `max_tokens=1`:

| Scenario | Time to first token |
|---|---|
| Large prompt, cold cache | 60 s |
| Identical prompt again | 0.4 s |
| Same prompt plus one appended turn | 1.1 s |
| Different prefix | 60 s |
| Back to the earlier conversation | 63 s |

Prefill runs at ~580 tokens/s and the server keeps **one** cached prefix. Every
request whose prefix differs from the last one pays the full prefill again. The
harness prompt alone (system prompt, tool schemas, instruction files, skill
descriptions) is ~14k tokens, so a cold turn costs ~25 s before the user's text is
read: that is the slow "hola".

The mid-session stalls have a different cause. opencode compacts a session when the
token count reaches `limit.context - min(limit.output, 32000)`, and a model that
declares no `limit` gets `limit.context = 0`, which disables the check entirely
(`isOverflow` returns false before counting). No `lmstudio-*` card in the tracked
`opencode.json` declares a limit. A session therefore grew past the 41k window, LM
Studio truncated the prompt from the middle on every turn, the prefix never matched
the cache again, and every call was a cold prefill.

Declaring the limit fixes the second problem for any window size. But the window is
not a property of the model: it is whatever the operator loaded in LM Studio (41k
today, 120k tomorrow), it changes without any file in this repository changing, and
a model loaded on demand (JIT) gets its own default instead. The lab already reads
that number for the run form ("loaded, 41k ctx", off `/api/v0/models`) and Ollama's
`/api/ps` reports the equivalent.

## Decision

We will read the served context window off the host at launch and declare it to
opencode as the model's `limit`, for every self-hosted model a session addresses.

- A new module, `harness_lab_app.context_window`, resolves a run-addressable id
  (`gateway/<alias>`, `lmstudio/<id>`, `ollama/<id>`) to its serving host — via
  LiteLLM's `/model/info` for a gateway alias, the card's `api_base` first and the
  lab's `LMSTUDIO_BASE_URL` / `OLLAMA_BASE_URL` as fallback — and reads the loaded
  window from that host. It returns nothing for a cloud model, an unloaded model, a
  dead host, or a window too small to declare (an 8k JIT default).
- `limit.context` is the served window minus headroom of 10 % (floor 4096 tokens),
  so the request that carries the next tool output still fits under the window the
  server enforces. `limit.output` is the gateway card's `max_tokens` when it has
  one, else 4096 (`HARNESS_LAB_DIRECT_OUTPUT_TOKENS` overrides it).
- The opencode overlay (`opencode_overlay.build_overlay`) accepts the limits and
  writes them onto the models the effective config already declares — never onto
  an unknown alias. Both launch paths use it: `scripts/bench_run.py` for lab runs,
  and `make opencode` through the new `scripts/opencode_context.py`, which prints
  `export OPENCODE_CONFIG_CONTENT=…` for the models named in `.env`.
- A detected window joins the `condition_hash` under the model's prefixed id and is
  recorded in `metadata.json` as `context_windows`. A session compacted at 41k and one
  compacted at 120k did not see the same context; they are different arms.
- `HARNESS_LAB_CONTEXT_AUTODETECT=0` disables it. `HARNESS_LAB_DISABLE_GATEWAY_PROBE`
  disables it too, so test suites and offline shells never reach for a host.

## Consequences

- Raising the window in LM Studio is now the whole procedure: the next `make opencode`
  or lab run declares the new limit, compacts inside it, and records it. No variable
  to keep in sync; no way for opencode and the server to disagree about the window.
- Compaction happens once, near the window, instead of the server truncating on every
  turn. A compaction still costs one cold prefill of the whole window (~1 min at 41k,
  ~3.5 min at 120k on this box). A larger window trades fewer compactions for a
  longer stall at each, and the cold first call is unchanged: that is prefill
  throughput and prompt size, addressed by the levers in
  `docs/local-model-runtime-config.md`, not by this decision.
- The `condition_hash` of a self-hosted run now moves when the served window moves.
  Historical runs (no detection, no limit) keep their hash: an absent limit adds
  nothing to the identity.
- A gateway alias whose card spells the host as `localhost` (the gateway's own view)
  is resolved through the lab's base URL instead. A card with no `api_base` and no lab
  variable for that backend is left undeclared, as before.
- Runs through the public gateway (Cloudflare in front) still hit its 100 s origin
  timeout on a cold prefill above ~55k tokens. Windows beyond that are for the direct
  lanes until the gateway is reachable on the LAN.

## Alternatives considered

- **Declare `limit` by hand in `opencode.json` / `.env`.** Correct until the operator
  changes the window in LM Studio, then silently wrong in the direction that
  reproduces this failure. The run form already knows the number; asking a human to
  copy it is the manual step this decision removes.
- **Raise `compaction.reserved` instead of `limit.context`.** `reserved` only acts
  when `limit.context` is non-zero; with no limit the check never runs.
- **Read the window at the gateway (LiteLLM `max_input_tokens`).** The gateway does
  not know what LM Studio loaded; its number is the model's nominal maximum (262k),
  which is exactly the wrong value.
- **Probe the window by sending an oversized prompt** (what the preflight's
  "Check context" does). Costs a full prefill on the GPU per launch and only
  answers "at least N", not the served number.

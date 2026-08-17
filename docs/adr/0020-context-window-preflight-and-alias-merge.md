# ADR-0020 — Context-window preflight and gateway alias merge

- **Status:** Accepted
- **Date:** 2026-08-10
- **Deciders:** Marcos Soto
- **Related:** ADR-0012 (dynamic gateway model list), ADR-0016 (opencode config
  overlay for model routing)

## Context

Every benchmark run against the self-hosted models started failing with opaque
stream errors, while the same gateway served ordinary chat traffic fine. Reading
the logs of the failed runs gave the answer, and it was not in this repository:

- **`tetris-v1-K-020`** — 2215 of its 2216 stream errors were the verbatim
  llama.cpp refusal
  `The number of tokens to keep from the initial prompt is greater than the
  context length (n_keep: 13777 >= n_ctx: 8192)`, each returned in a p50 of
  **0.4 s**. LM Studio had JIT-auto-loaded the model with its **default** 8k
  window — the hand-set context length applies to the model you load, not to one
  loaded on demand. The harness system prompt is ~13.7k tokens before the first
  user turn, so every one of the run's 4432 steps was rejected before evaluation.
  2.5 hours of wall clock and a 12 MB log for zero work.
- **`tetris-v1-K-021` / `K-019`** — 83 and 198 `SchemaError(Missing key at
  ["content"])` on the `write` tool. Isolated probes showed the same models emit
  valid arguments, so this is degradation under long context, not a harness bug.
- **`AI_APICallError: <none>`** — a cold JIT load (measured at 42.5 s) outrunning
  opencode's stream timeout.

Two problems made this expensive to diagnose. First, the *loaded* window is not
discoverable: `GET /v1/models` and LiteLLM's declared `max_input_tokens` describe
configuration, not what the backend is serving right now. Second, the model list had grown to 51 entries, several of which
`/model/info` shows to be indistinguishable — `claude-opus-5`,
`claude-opus-5-oauth` and `claude-opus-5[1m]` carry byte-identical
`litellm_params`, the same `max_input_tokens` and the same backend. They read as
distinct experimental arms and are not.

The `-build` cards are the opposite case and the reason the merge key had to be
widened: `lmstudio-qwen3.6-35b` and `lmstudio-qwen3.6-35b-build` share a target
and `api_base` but ship different sampling (0.7/top_p 0.8 against 0.2/top_p 0.9
with frequency and presence penalties and a 4096-token output cap). Merging on
target alone would have pooled two real conditions into one row.

A pre-existing bug compounded it: `probe_gateway_model` sent the
`gateway/`-prefixed id to LiteLLM, which answers HTTP 400 "Invalid model name".
The probe only reacts to 5xx and connection markers, so it silently returned
"looks fine" for every model it had ever been asked about.

## Decision

**1. Measure the served context window with a real oversized prompt.**
`probe_model_context()` sends filler sized to `MIN_CONTEXT_TOKENS` and reads the
answer. There is no cheaper honest signal — the refusal is the only place the
backend states `n_ctx`.

- `MIN_CONTEXT_TOKENS = 32768`, chosen from measurement rather than taste: 13.7k
  is consumed before the first user turn, so 32k is the smallest ceiling that
  still leaves working headroom for a multi-step run.
- The filler is a varied word list, not one repeated token: repetitive text
  compresses to far fewer tokens and would make the probe claim a window it never
  proved. The chars-per-token ratio still differs per model, so a request that
  lands short of the requirement is **measured and retried once** with the
  corrected ratio instead of being reported as a shortfall the user cannot act on.
- `max_tokens` is 64, not 1. Reasoning models reject a tiny output budget
  outright; a complaint about the *output* budget proves the *input* was accepted,
  so it is classified as a pass.

**2. Fail open.** The verdict is `ok` / `insufficient` / `unknown`, and only
`insufficient` — a refusal that names a window smaller than required — blocks a
run. Timeouts, cold JIT loads, rate limits and an unreachable gateway all yield
`unknown` and let the run proceed. A preflight that guesses wrong in the blocking
direction is worse than no preflight.

**3. Gate run creation, and expose the same measurement on demand.**
`RunService._check_executor_context()` runs at run creation for `gateway/`
executors (memoized 600 s, conclusive results only, skipped by
`HARNESS_LAB_DISABLE_GATEWAY_PROBE`). `POST /api/models/{id}/context-check` drops
the memo and measures now, behind a **Check context** button in Settings → Model
catalog.

`RunService.preflight()` is public, and `POST /api/runs/validate` calls it and
returns `{ok, detail}` instead of raising — the **Validate config** button on the
run form. The gate must be answerable *before* the form is submitted, or the user
meets it as a rejected submission; the same memo means a green answer is free to
act on.

**4. Merge aliases by routing identity, not by name.** The identity of a *served*
model is its `litellm_params.model` + `api_base` **plus every behaviour-affecting
parameter** LiteLLM reports (temperature, top_p, penalties, `max_tokens`,
timeouts) — everything except the bookkeeping flags present on all entries
(`use_litellm_proxy`, `use_in_pass_through`, `use_xai_oauth`,
`merge_reasoning_content_in_choices`). Ids sharing that whole identity collapse
into a single catalog entry, with the swallowed names returned under `aliases`.
The surviving name is the one the harness can already route (configured default
first, then anything declared in `opencode.json` or the web overlay), so merging
never silently repoints an existing arm; ties break on the shorter name. An alias
`/model/info` cannot describe keeps its own group, so an unreachable endpoint
degrades to today's flat list rather than merging unrelated models.

Credentials are the one thing `/model/info` does not expose, so an `-oauth`
alias merges with its API-key twin. That is deliberate: they produce the same
model behaviour, and the lab compares behaviour, not billing.

**5. Send bare ids to LiteLLM.** Both probes now call `normalize_model_name()`.

## Consequences

- **A run can now be refused for a reason the gateway reports as healthy.** That
  is the point, but it means `HARNESS_LAB_DISABLE_GATEWAY_PROBE=1` is the escape
  hatch if a backend ever phrases a refusal in a way the classifier reads wrong.
- **Run creation costs one extra request** against the executor's backend, and on
  a cold model that request pays the JIT load (~40 s observed). The memo keeps it
  to once per model per 10 minutes; the timeout is 90 s at the gate and 180 s
  behind the button.
- **The dropdown shrank from 51 entries to 46** on the current gateway — only the
  provably identical Claude aliases merged. Any run metadata already recording a
  swallowed alias keeps working: merging changes what is *offered*, never what is
  routable, and the alias remains a valid id at the gateway.
- **The catalog now depends on `/model/info`**, which needs a key allowed to read
  it. Without it the list is correct and merely long.
- **The list stays long on purpose.** A shorter list was the request; a wrong
  merge would corrupt every comparison that used it, so the rule errs toward
  showing two rows whenever the gateway reports any behavioural difference.

## Alternatives considered

- **Trust `max_input_tokens` from `/model/info`.** Free, and wrong exactly when it
  matters: it is configuration, and K-020's model declared a large window while
  serving 8k.
- **Warn instead of block.** The failure is silent for hours and the log looks
  like a network problem; a warning in a form the user has already stopped reading
  does not prevent 2.5 wasted hours.
- **Merge aliases by name heuristics** (strip `-build`, `-mac16`…). Cheap, but it
  guesses; two ids with unrelated names can share a backend and two similar names
  can carry genuinely different parameters. Routing identity is the fact.
- **Raise `MIN_CONTEXT_TOKENS` to 64k.** Safer for long runs, but it would refuse
  backends that do complete work today. 32k is the floor this harness provably
  needs; the doc recommends 64k where the GPU allows.

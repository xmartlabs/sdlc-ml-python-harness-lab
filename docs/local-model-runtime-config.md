# Local Model Runtime Config (the mechanical layer)

The most important document for running a small self-hosted model (Qwen 3.x / 3.6, 27B-35B)
through a coding agent. **Prose rules in instruction files are not enough** — a small model
cannot reliably prioritize them over the agent's own large built-in prompt (GitHub Copilot's
is ~9k tokens and tells the model to do the opposite: gather lots of context, read large
chunks, never give up).

The fix is to move the binding constraints from **soft** (prose the model may ignore) to
**hard** (limits the runtime enforces mechanically). This doc lists those hard levers. It
complements two docs that already cover the *input/convergence* side: the "won't converge"
checklist in [`.github/portability.md`](../.github/portability.md) and the sizing heuristics
in [`docs/task-sizing.md`](task-sizing.md).

## Priority order (start here)

When you want to develop with self-hosted models via the ai-gateway, apply these in order.
The top items are **mechanical** (enforced no matter what the model does); the bottom are
**discipline**. This ordering is the whole playbook — everything below is detail.

1. **Driver: OpenCode through the gateway, not Copilot.** `make opencode` with
   `OPENCODE_MODEL=gateway/…-build`. Copilot agent mode's fixed overhead saturates a 32k
   window before you type; OpenCode has a small prompt + fresh context per phase. (Lever:
   "Drive it through OpenCode" below.)
2. **Cap output at the gateway** (`max_tokens` on the `-build` card, ~4096). Makes full-file
   rewrites and `Response too long` mechanically impossible. (Lever 1.)
3. **Repetition penalty + temperature 0.2** on the `-build` card. Kills the infinite
   "Let me read the file… / Actually let me read…" loop — low temp *alone* makes it worse.
   (Lever 6.)
4. **Serve enough context** — 32k minimum, 64k if the GPU allows. (Lever 2.)
5. **One increment per phase/chat** — plan → build → validate → next, fresh context each step
   so the conversation never compacts. (Lever 5.)
6. **Keep the instruction surface tiny** — `LOCAL_AGENT.md` only for build. More rules = worse
   adherence on a small model. (Lever 4.)
7. **Plan with a cheap-but-strong cloud model, build local.** Plan = `gateway/gpt-5.5`
   (small spend; Claude/Bedrock only as last resort, ~2x); build = `gateway/…-build` ($0,
   visible in Langfuse). Bulk tokens stay self-hosted.

Items 1-4 + 6 are set once in `opencode.json` / `.env` / the gateway `config.yaml`; items 5
and 7 are how you run it day to day.

## Two distinct failure modes — they need different fixes

| Symptom (in Langfuse / the gateway) | Failure mode | Fix layer |
|---|---|---|
| Input ~33-36k tokens, outputs are `Chronological Review` summaries, model "loops" | **Input saturation → compaction** | served context + short instructions + fresh chats — see portability checklist |
| `Response too long` / `the response hit the length limit`, output > ~10k tokens | **Output overrun** (full-file rewrite) | **gateway `max_tokens` cap** (below) |
| After many turns: `Compacted conversation`, then a long "what's implemented / missing" summary; the model loses the code state | **Late-conversation compaction** | **one increment per chat** (lever 5) + no-inventory rule |
| The same sentence/decision is repeated over and over until the turn dies (often ending in `Response too long`) | **Decoding repetition loop** (a sampling pathology, not reasoning) | **repetition penalty** + a live output cap (lever 6) — no instruction can fix this |

The convergence checklist in `portability.md` addresses the first (an **input** loop). This
doc adds the second: `Response too long` is an **output** limit. No Copilot setting and no
instruction-file rule caps output — the only control point you own is the gateway.

## Hard lever 1 — cap output tokens at the LiteLLM gateway (the real fix)

If you front the local model with a LiteLLM gateway (e.g. for Langfuse tracing), cap
`max_tokens` per model in `config.yaml`. A capped response cannot overrun Copilot's response
ceiling, so `Response too long` becomes mechanically impossible — and the cap *forces* the
small, targeted edits the workflow wants.

```yaml
# litellm config.yaml (your gateway, not this repo)
model_list:
  - model_name: qwen3.6-35b           # the id Copilot/LM Studio targets
    litellm_params:
      model: lm_studio/qwen3.6-35b
      api_base: http://<lmstudio-host>:1234/v1
      max_tokens: 2048                 # HARD cap — a full-file rewrite (~10k) is now impossible
      temperature: 0.2                 # low, deterministic edits

litellm_settings:
  drop_params: true                    # silently drop params the local backend rejects
```

- **Why ~2048:** a targeted edit (the only thing this workflow should produce per turn) fits
  easily; a whole-file regeneration does not. The cap converts "never rewrite the file" from
  a hope into a guarantee.
- Raise to ~3072 only if legitimate single edits get truncated. Do **not** set it to 8k+ —
  that re-enables the failure.
- No gateway? LM Studio's per-model "max tokens to generate" (server settings) is the
  fallback, but the gateway is the reliable place: Copilot sets the per-request `max_tokens`
  and the gateway can override it.

## Hard lever 2 — served context window

Copilot agent mode's own system prompt consumes ~9k tokens before your code is read. At a 32k
window that leaves ~20-22k of effective working context — tight for anything beyond a single
file. This is the same lever as #1 in the `portability.md` convergence checklist, applied to
Copilot specifically:

- **Serve 32k minimum.** If the GPU allows, serve **64k** — 32k-64k is the practical floor for
  Copilot agent mode; 32k is the edge.
- Set context length in LM Studio (KV cache) / Ollama `num_ctx`; confirm the served id matches
  `provider.lmstudio.models` in `opencode.json` and the gateway `model_name`.
- Temperature 0.2 (set at the gateway per lever 1, or in LM Studio / `opencode.json`).

### The JIT auto-load trap (read this before blaming the model)

The context length you set by hand in LM Studio applies to the model **you loaded**. When a
request arrives for a model that is *not* loaded, LM Studio loads it on demand (JIT) using
that model's **default** window — often 8k — and your setting is not consulted. The gateway
answers normally, so nothing upstream looks broken.

The harness system prompt (governance docs, 24 skill descriptions, 6 agent definitions and
the tool schemas) is **~13.7k tokens before the first user turn**. Under an 8k window every
call is rejected by llama.cpp *before evaluation*:

```
The number of tokens to keep from the initial prompt is greater than the
context length (n_keep: 13777 >= n_ctx: 8192)
```

Run `tetris-v1-K-020` hit exactly this: 4432 steps, each failing in ~0.4s, 2.5 hours of wall
clock and a 12 MB log for zero work. The rejection is instant, which is what makes it look
like a stream error rather than a configuration problem.

The runner container is also headless *by construction* now: `SDL_VIDEODRIVER=dummy`
and `SDL_AUDIODRIVER=dummy` are set in the image, in the run environment, and in
`bench_run` itself, so a case no longer depends on the agent retyping the prefix
([ADR-0021](adr/0021-benchmark-artifact-integrity.md)). Measured compliance before that
was 72% and 40% on two runs, and every miss left a pygame loop holding the agent's
shell open until the run wedged.

Two things in the lab now close this:

- **Preflight gate.** Creating a run with a `gateway/` executor sends one oversized prompt
  and refuses to start if the backend proves its window is under **32768** tokens
  (`MIN_CONTEXT_TOKENS`). Only a *provable* shortfall blocks — a timeout, a cold JIT load or
  an unreachable gateway all fail open. `HARNESS_LAB_DISABLE_GATEWAY_PROBE=1` skips it.
- **“Check context” button** in Settings → Model catalog, which runs the same measurement on
  demand and reports the window the backend is actually serving.

The reliable fix on the LM Studio side is to load the model explicitly with the window you
want (or raise its default in the model's config) before launching a run, rather than
letting JIT pick it.

## Prompt processing time and the run watchdog

A local model says **nothing at all** while it processes the prompt. That silence is not
idle time, and it grows with the prompt: measured against the box serving
`qwen/qwen3.6-35b-a3b`, a trivial prompt returns its first token in **0.2s** and the
harness's ~24k-token prompt with tool schemas takes **318s**. The same box serving a 9B
model never exceeded 76s. Nothing is wrong in either case — the big model is simply
prefilling.

This matters because the lab kills quiet runs. Three settings decide when, and they
measure different silences (`.env`, `HARNESS_LAB_` prefix; `0` disables any of them):

| Setting | Default | Kills a run when |
|---|---|---|
| `run_stall_seconds` | `600` | It has been quiet this long **with no model request in flight** — i.e. blocked in a tool call that will never return |
| `run_model_wait_seconds` | `2400` | It has been quiet this long **waiting on the model's first token** |
| `run_max_stream_errors` | `5` | This many provider requests failed back-to-back with no progress in between |

The watchdog tells the two silences apart by reading opencode's own log: a run sitting on
`message=stream` / `llm runtime selected` is waiting on the provider, anything else is the
agent's own doing. Before that split there was one 600s clock for both, and it sat *below*
the 35B's prefill time — so every 35B run died at its first turn (`K-040`, `K-049`, `K-050`,
all in the same place) and was filed as the agent wedging the harness, while the faster 9B
sailed through. It read as "the big model broke"; it was a threshold set too low
([ADR-0022](adr/0022-watchdog-splits-model-wait-from-agent-stall.md)).

**Reading the verdict:**

- `Run stalled:` — the agent is stuck in a tool call. A real result; it counts as an attempt.
- `No model response:` — the endpoint accepted the request and sent nothing back. If the
  model is merely slow, raise `run_model_wait_seconds`; if it is wedged, check that the model
  is loaded and that its window fits the prompt (the JIT trap above). Does **not** count as an
  attempt.
- `Model endpoint failing:` — the provider refused repeatedly; the last error travels with the
  verdict. Check reachability from the runner container and the model id. Does **not** count
  as an attempt.

Raise `run_model_wait_seconds` when you serve a model slower than anything measured here
(a larger model, a longer prompt, a colder cache). Keep it below `run_timeout_seconds`
(default 7200), or the total budget becomes the only thing that ever stops a wedged run.

## The direct lanes (skip the gateway when you are iterating)

Everything above assumes the gateway is in the path. It buys Langfuse tracing and per-model
`max_tokens` caps — worth it for a benchmark run you intend to keep. It also adds a name
mapping, and **when that mapping is wrong the failure is silent**: run `tetris-v1-K-031` used
a LiteLLM alias that forwarded the id `qwen3.6:35b`, LM Studio answered `Invalid model
identifier`, and the run-form preflight — which treats an unfamiliar 4xx as ambiguous —
reported the configuration ready. A day of runs went to a name mismatch three layers away
from the picker.

For iterating over models quickly, the lab has a second, shorter path.

**Setup.** One variable, on Settings → Local runtimes:

```
LMSTUDIO_BASE_URL=http://192.168.1.50:1234/v1
```

The address of the machine running LM Studio, **including `/v1`** (opencode talks to LM
Studio's OpenAI-compatible endpoint; without the segment every completion 404s). In LM Studio,
Developer → *Serve on local network* must be on if the lab runs on another host. No gateway
entry, no `opencode.json` edit, no catalog refresh.

**What you get.** A group at the top of the run form's model picker, **LM Studio (direct, no
gateway)**, and a matching panel on the Settings page. Both are read straight from
`GET /api/v0/models` on that host, which reports per model whether it is loaded and with what
context window:

```
prism-ml/bonsai-27b — loaded, 54k ctx
qwen/qwen3.6-35b   — not loaded (JIT would use its default window)
```

Runs address these as `lmstudio/<publisher>/<model>`, and the routing provider is written
into the same gitignored opencode overlay the gateway catalog uses, so nothing tracked
changes. Press **Refresh from LM Studio** after loading or ejecting a model; the list is
cached for 15 seconds and failures are never cached.

**Why the preflight is strict here.** The gateway checks fail open by design — a timeout may
be a cold JIT load, and blocking a runnable config is worse than letting it try. The direct
lane has no such ambiguity: one cheap GET answers *does this name exist*, *is a model behind
it*, and *how big is its window*. So it blocks, immediately and with the reason, when

- `LMSTUDIO_BASE_URL` is unset or lacks `/v1`,
- the host does not answer,
- the name is not one the server serves (the error lists the ones it does),
- the model is not loaded, or
- its loaded window is under `MIN_CONTEXT_TOKENS` (32768).

None of these takes measurable time, which is the other half of the point: the gateway
context probe sends a 32k-token prompt and a dense 27B model can spend minutes evaluating it.

**Which lane to use.** Direct while you are comparing models or debugging a case; through the
gateway for runs whose traces and costs you want in Langfuse. The two coexist — a run records
whichever executor id it used, so the leaderboard tells them apart.

### The same, for Ollama

`OLLAMA_BASE_URL` gets you a second direct provider, listed from `GET /api/tags` (what is
installed) and `GET /api/ps` (what is running, and with what window):

```
OLLAMA_BASE_URL=http://192.168.1.50:11434/v1
```

Same rules: include `/v1`, and if Ollama runs on another machine it must listen on the
network — start it with `OLLAMA_HOST=0.0.0.0`, or it only binds loopback and the lab sees
nothing. Runs address these as `ollama/<model>:<tag>`.

**The context trap is different here, and worse by default.** LM Studio's JIT auto-load takes
each *model's own* default window. Ollama applies one *server-wide* setting,
`OLLAMA_CONTEXT_LENGTH`, whose default is **4096** — below the ~13.7k-token system prompt, so
a stock Ollama host cannot run this harness at all. Nothing in the OpenAI-compatible request
can override it (`num_ctx` is not an OpenAI parameter), so it has to be fixed on the server:

```bash
OLLAMA_CONTEXT_LENGTH=32768 ollama serve     # or set it in the systemd unit / service env
```

Because that setting lives on the server, the preflight treats a cold Ollama model
differently from a cold LM Studio one: LM Studio refuses to start (its window is unknowable),
while Ollama lets the run proceed and the picker label warns instead — blocking on a number
the lab cannot read would ground a correctly-configured host. A *running* Ollama model whose
`/api/ps` window is under 32k is refused exactly like an LM Studio one, since there the
shortfall is proven.

## Hard lever 3 — VS Code / Copilot settings

`.github/copilot-instructions.md` is auto-detected and applied to every chat request; keep it
short (see lever 4). Recommended workspace settings (`.vscode/settings.json`):

```jsonc
{
  // ensure .github/copilot-instructions.md loads on every request
  "github.copilot.chat.codeGeneration.useInstructionFiles": true,
  // a one-line always-on nudge in addition to the instruction file
  "github.copilot.chat.codeGeneration.instructions": [
    { "text": "local_model_32k: follow LOCAL_AGENT.md — edit not rewrite, one thing per turn, small output, whole-file reads, then stop." }
  ]
}
```

> Note: `.vscode/` is gitignored in this template, so the file above does **not** travel with
> the repo — create it locally on each machine, or un-ignore `.vscode/settings.json` if you
> want it shared. The snippet is kept here so the setting survives regardless.

Also:

- Prefer **Edit/Agent mode with one file open** over multi-file context to reduce prompt size.
- Start a **fresh chat per increment** — once a conversation is compacted, the model has
  already lost the code state. A new chat with a focused prompt resets the window.

## Hard lever 4 — keep the instruction surface tiny

A 27B-35B model follows **5 blunt rules better than 25 scattered ones**. Every token added to
`LOCAL_AGENT.md` or the Copilot instructions competes for attention against Copilot's built-in
prompt. The instruction files are deliberately short; resist adding to them. Put rationale and
config *here*, not in the files the model loads every turn.

## Hard lever 5 — one increment per chat (stop compaction)

The failure that survives every lever above: a chat that runs many increments eventually gets
**auto-compacted**, and after compaction the model has lost the real code state and starts
re-deriving it. The tell-tale sign is a long "what's implemented / what's missing" summary,
often self-contradictory. No in-conversation rule prevents this — the long conversation *is*
the problem.

- **Start a fresh chat per increment.** State lives in the file, not the chat history. A new
  chat with a one-line "do increment N, then stop" prompt never grows long enough to compact.
- The model cannot open a chat for you, so it should **end each increment with a STOP banner**
  cueing the new chat. The human action (opening it) is the actual lever.
- On resume in an already-long chat, the model must **not inventory the file** — re-read only
  what the next increment needs and continue.

## Hard lever 6 — sampling settings (what stops the infinite repetition loop)

If the model emits the **same sentence or decision over and over** ("Let me read the file. /
Actually let me read the file. / OK let me read the file…") until the turn dies, that is a
**decoding pathology**, not a reasoning failure — and **no instruction can fix it** (a model
stuck in a repetition loop cannot obey "don't repeat"). Two serving-layer settings cure it:

- **Repetition penalty ~1.1-1.2** (LM Studio: *Repeat Penalty*; or `repetition_penalty` /
  `frequency_penalty` ~0.3-0.5 at the gateway). This is the direct fix. **Low temperature
  alone makes loops *worse*** — you need a penalty, not just low temp.
- **The lever-1 output cap must actually be set.** If a repetition loop reaches Copilot's
  `Response too long`, the gateway `max_tokens` cap is **not** in effect — a live 2048 cap
  would have truncated the loop long before. Reaching `Response too long` is proof the cap was
  never applied.

> **Spike size ceiling.** "Read the whole file in one call" assumes a small file. Once a spike
> passes ~250-300 lines — or you see duplicated definitions because the model *appended*
> instead of editing — it has outgrown what a small local model can safely hold and edit
> through Copilot. Reset to a smaller file, or promote it to a real `feature` you drive with a
> stronger model. Do not keep vibe-coding a large file with the local model.

## Drive it through OpenCode, not Copilot

Copilot agent mode is the worst-case harness for a 32k local model: its ~9k-token system
prompt + tool schema + workspace context means even a fresh chat starts near the ceiling, and
its built-in prompt pushes the *opposite* of the local-model discipline (gather lots of
context, read large chunks). OpenCode fits far better:

- **Smaller fixed overhead** — much less preamble per request, so more of the 32k is yours.
- **Real plan/build split** — `plan` (read-only) and `build` (full tools), switched with `Tab`.
- **Fresh context per phase** — no single conversation holds the whole task, so it never grows
  into the late-session compaction loop that breaks Copilot.

Route OpenCode through the gateway so the build profile (cap, temp 0.2, penalties) is enforced
and every call shows in Langfuse:

1. `.env`: `OPENCODE_MODEL=gateway/lmstudio-qwen3.6-35b-build` (build, self-hosted),
   `OPENCODE_MODEL_PLAN=gateway/gpt-5.5` (plan, strong cloud for the small backlog; Claude is
   last resort — Bedrock, ~2x the cost),
   `GATEWAY_BASE_URL=http://localhost:4000/v1`, `GATEWAY_TOKEN=<your LITELLM_MASTER_KEY>`.
2. `make opencode-doctor` — confirms the gateway answers.
3. `make opencode` — in **Plan**, ask for a small increment backlog; `Tab` to **Build**,
   execute ONE increment with the local model, validate (`make run` / tests), then the next.
4. Watch **Langfuse**: build calls are the local model at $0; plan is a small cloud spend.
   That split is the point — bulk tokens stay self-hosted and visible.

Switch the build model anytime with `/models` (e.g. `gateway/lmstudio-qwen3.6-27b-build` for a
faster executor).

## Honest ceiling

These six levers remove the *harness* failures (input saturation, output overrun, compaction,
repetition loops). What they cannot do is raise the model's own capability. A 27B-35B model at
32k will still: lose the thread on a 400-line file, append instead of editing, and need the
hard logic spoon-fed in tiny pieces. Use it for small spikes and mechanical edits; for a
correct, complete artifact, plan and do the hard parts with a stronger model and let the local
one fill in the small, well-scoped steps.

## Why soft rules alone fail (for the record)

Copilot's built-in system prompt (visible in any Langfuse trace) includes:

> "you can call tools repeatedly to take actions or gather as much context as needed until you
> have completed the task fully. Don't give up..."
> "When reading files, prefer reading large meaningful chunks rather than consecutive small
> sections..."

These directly contradict the local-model discipline (one increment, small reads, small
output) and they arrive first and with authority. You cannot edit them. Hence: **enforce
mechanically (levers 1-3), instruct minimally (lever 4).**

## Verification loop

1. Apply lever 1 (gateway cap) and lever 2 (context).
2. Open a fresh Copilot chat and give it one scoped change (one bug or one increment, with an
   explicit "fix only this, then stop").
3. Watch the run in Langfuse: **output tokens should stay under your cap** (~2k) and there
   should be no `Response too long`. Input should stay well under the served context.
4. If output still spikes, the gateway cap is not being applied — verify the `model_name`
   matches what Copilot sends and that `config.yaml` is loaded.

This layer is model-agnostic and task-agnostic: it works for any small self-hosted model and
any software task.

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

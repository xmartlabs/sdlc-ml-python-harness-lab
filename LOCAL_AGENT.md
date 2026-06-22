# Local Agent Rules (`local_model_32k`)

> Opt-in mode for small self-hosted models (Qwen 3.x / 3.6, 27B-35B @ 32k) via Copilot /
> OpenCode / LM Studio. Short on purpose — do not expand it. The binding limits are enforced
> mechanically at the gateway, not here: see
> [`docs/local-model-runtime-config.md`](docs/local-model-runtime-config.md).

## Rules (in priority order)

1. **Edit, never rewrite.** Change only the wrong lines. Never regenerate a whole file — a
   full rewrite overruns the response limit and the turn fails with "Response too long".
2. **One thing per turn, then stop.** One bug or one increment. If asked to "fix everything",
   fix the single root cause, list the rest deferred (one line each), and stop.
3. **Keep output small.** Aim under ~60 lines total: a 3-line preamble, the edit, one result
   line. No analysis blocks, no summaries, no "Chronological Review" prose.
4. **Read whole, in one call.** `read_file` with no line range — one call per file. Never
   read in 100-line chunks.
5. **Declare before editing:** `Target file:` / `Expected change:` / `Validation:` (the exact
   command). Then the edit. Then the result line.
6. **Runnable first.** Reach the minimal thing that runs before adding features. No
   clean-architecture layering or scaffolding for spikes — one module until it runs
   (see [`docs/task-sizing.md`](docs/task-sizing.md), `spike` tier).
7. **Use the edit tool, not a code block.** Apply changes with the edit tool; do not paste
   full files into chat.
8. **One increment per chat — then start a NEW chat.** End each validated increment with one
   line: `✅ DONE: <what works>. NEXT: <one line>. Open a NEW chat to continue.` Do not run
   many increments in one chat: long chats get auto-compacted and you lose the code state —
   the #1 cause of late-conversation breakage.
9. **Never inventory the file.** Do not output "what's implemented / what's missing"
   summaries. If context was compacted, re-read only the part needed for the single next
   increment and continue — never re-derive the whole file.

## "Build the whole thing" → a loop, not one generation

When asked to build something large in one go, do not. Decompose it into the smallest
increments that each run or test, and execute only the next one, then stop. Reach a runnable
artifact before adding features.

Sizing heuristics: [`docs/task-sizing.md`](docs/task-sizing.md). Why these rules and how to
enforce them mechanically at the gateway:
[`docs/local-model-runtime-config.md`](docs/local-model-runtime-config.md).

# Harness: antigravity (human-driven)

Run the benchmark task with **Google Antigravity** (the IDE or panel) to evaluate and
improve the Google-case rules: `.agents/rules/GEMINI.md` and the skills in `.agents/skills/`.

This flow is **human-driven**: execution is not automated and there is no step-by-step agent
log. It prepares the workspace, lets you drive Antigravity by hand with your own sign-in on your
own machine, and then uses an LLM to propose prescriptive edits to the `.agents` rules based on
how the model behaved.

> **Automated runs use a different harness.** Google now ships a headless Antigravity CLI
> (`agy`), and the lab runs it as **`antigravity_cli`** under the same cases, containers,
> scoring and comparison as codex/claude/opencode. It runs on your Google sign-in, made by
> `agy` itself from System → Agent CLIs, on your plan's quota
> ([ADR-0097](../docs/adr/0097-antigravity-cli-on-the-operators-google-sign-in.md)), or on a
> Gemini API key ([ADR-0096](../docs/adr/0096-antigravity-cli-runs-on-a-gemini-api-key.md)).
> See the [runbook](../docs/runbook-claude-codex.md#antigravity-cli-on-your-google-plan),
> including Google's note on consumer plans. Keep this manual flow for what it is for:
> driving the IDE yourself and improving the `.agents` rules. Both read the same
> `.agents/rules/GEMINI.md` and `.agents/skills/`.

This cycle is fully separate from the opencode flow (`bench_run.py`, `opencode.json`). It writes
no `BENCH_CONTEXT.md`, launches no tool, and creates no gateway virtual key.

## Cells

`harness: antigravity`, `methodology: antigravity-ide-human` (cell N in `tetris-v1`). The
`executor_model` / `planner_model` fields are labels — set them to the actual Gemini model
Antigravity uses. To compare the IDE vs the standalone panel, add another cell with a different
`methodology` (e.g. `antigravity-panel-human`).

## Procedure (4 steps)

1. **Prepare** — `make bench-antigravity RUN=tetris-v1-N-001`
   Registers the run, creates `workspace/<id>/`, writes `prompt.md`, scaffolds `transcript.md`
   and `notes.md`, copies the prompt to the clipboard, and prints instructions. It does not
   launch anything.

   The N cell defaults to the **`sdlc_full`** prompt: one prompt that drives the full build of
   all 8 milestones in a single autonomous session (Antigravity is agentic, so it runs straight
   through — there is no harness loop to advance milestones like opencode has). To run only
   Milestone 1 (a single opencode-style step), pass `PROMPT=sdlc`.

2. **Run in Antigravity (by hand)** — Open the project in Antigravity (IDE or panel). It reads
   `.agents/rules/GEMINI.md` + `.agents/skills/` automatically. Paste the prompt, work the task
   inside `workspace/<id>/`. Capture what you can (graceful degradation — the audit uses whatever
   is present):
   - Paste the Agent Manager transcript/walkthrough into `data/runs/<id>/transcript.md`.
   - Fill `data/runs/<id>/notes.md` (did it read GEMINI.md? which skills? where did it deviate?).
   - Drop screenshots into `data/runs/<id>/`.

3. **Close** — `make bench-close RUN=tetris-v1-N-001 SCORE=<0-10> INTERVENTIONS=<N>`
   Generic close: generates `diff.patch`, copies the workspace, updates `runs.yaml` /
   `results.csv`. Score with the same `scoring.md` rubric (run the game, observe, 0–10).

4. **Audit the rules (prescriptive)** — `make bench-agents-audit RUN=tetris-v1-N-001`
   An LLM reads the run evidence vs the current rules and writes
   `data/runs/<id>/agents_audit.md` with concrete proposed edits to `GEMINI.md` and the skills,
   citing evidence and separating high-confidence findings from hypotheses to validate.

Prefer the opencode harnesses for cells that must stay automated/comparable; use this one to
specifically improve the Antigravity (`.agents`) rules.

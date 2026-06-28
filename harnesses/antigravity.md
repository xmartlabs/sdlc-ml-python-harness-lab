# Harness: antigravity (human-driven)

Run the benchmark task with **Google Antigravity** (the Gemini IDE or panel) to evaluate and
improve the Google-case rules: `.agents/rules/GEMINI.md` and the skills in `.agents/skills/`.

Antigravity is **human-driven** — there is no headless CLI like opencode, so execution is not
automated and there is no step-by-step agent log. This harness prepares the workspace, lets you
drive Antigravity by hand, and then uses an LLM to propose prescriptive edits to the `.agents`
rules based on how the model behaved.

This cycle is fully separate from the opencode flow (`bench_run.py`, `opencode.json`). It writes
no `BENCH_CONTEXT.md`, launches no tool, and creates no gateway virtual key.

## Cells

`harness: antigravity`, `methodology: antigravity-ide-human` (cell N in `tetris-v1`). The
`executor_model` / `planner_model` fields are labels — set them to the actual Gemini model
Antigravity uses. To compare the IDE vs the standalone panel, add another cell with a different
`methodology` (e.g. `antigravity-panel-human`).

## Procedure (4 steps)

1. **Prepare** — `make bench-antigravity RUN=tetris-v1-N-001`
   Registers the run, creates `workspace/<id>/`, writes `prompt.md` (resolved from the run's
   `prompt_variant`, e.g. `tetris_sdlc_v1` for comparability with cells K/L/M), scaffolds
   `transcript.md` and `notes.md`, copies the prompt to the clipboard, and prints instructions.
   It does not launch anything.

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

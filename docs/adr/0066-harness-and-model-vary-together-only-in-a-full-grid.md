# ADR-0066: Harness and model vary together only in a full grid

- **Status:** Accepted
- **Date:** 2026-10-21
- **Deciders:** Marcos Soto
- **Related:** [ADR-0028](0028-evaluation-modes.md),
  [ADR-0051](0051-the-harness-ladder-is-one-family-of-single-factor-experiments.md),
  [ADR-0053](0053-an-experiment-is-read-back-as-prose.md),
  [ADR-0064](0064-an-experiment-shows-its-uncertainty.md),
  [docs/plans/2026-10-harness-ecosystem-analysis-and-improvement-plan.md](../plans/2026-10-harness-ecosystem-analysis-and-improvement-plan.md)
  (Phase 3)

## Context

The question 2026 keeps asking about harness engineering is whether a stronger model
needs less of it. Every component of a harness encodes an assumption about what the
model cannot do on its own; when the model improves, some components stop earning their
tokens. Research this year found context files and skills help far less than assumed,
and that their value depends on the model.

`harness_evaluation` cannot answer it, by design: it holds the model fixed and refuses a
design where the model varies, because then a difference between two arms cannot be
attributed to the harness. That refusal is right for its question and wrong for this
one. "Does the harness help less on a bigger model?" is not a difference between two
arms — it is an **interaction**: how the harness effect changes as the model changes.
It can only be read from a grid where every harness level runs on every model.

## Decision

**A fifth mode, `harness_by_model`, accepts exactly one design: a full grid of harness
levels × models, varying one harness factor.**

1. **Validation** (`factorial.grid_problems`, called from `validate_design`) refuses:
   fewer than two models or two harness levels; any missing cell; a cell declared twice;
   more than one harness factor varying (`harness_source`, `prompt_variant`, `skills_*`
   or `harness`); anything else varying (repository, ref, planner, step budget).
2. **No pairwise comparison.** The summary renders no control-versus-treatment delta in
   this mode — any pair of cells would mix model and harness. The control role is kept
   on one arm so the record is well formed, and ignored.
3. **The `factorial` block** (`factorial.analyze`) reports, per model, each harness level
   against the first, and the **interaction**: the top level's effect on the last model
   minus its effect on the first. Order is the order the conditions were declared, not
   the read order (which sorts the control first and would flip the sign).
4. **Intervals.** One paired, case-clustered bootstrap draws the cases once and applies
   the same draw to every cell, because every cell shares a case's difficulty. Each
   effect's interval is widened to at least Newcombe's; the interaction's to at least the
   two Newcombe intervals combined in quadrature. A bootstrap over unanimous cells
   resamples to a single value and would read as certainty; the analytic floor prevents
   it. The combined floor is conservative — it can extend past what the design can
   reach — which is the intended failure direction.
5. **"Established" needs three paired cases** and an interval that excludes zero. With
   fewer cases there is no between-case variation to resample, so the reading says the
   change is not established, whatever the interval.
6. **Cost per resolved task** is reported per cell, so "Haiku with the harness resolves
   at $0.08, Opus without it at $1.01" is on the page beside the effects.
7. **The reading** is one sentence the intervals support. It never says *significant*,
   *proves* or *causes* (ADR-0053).

The UI exposes it as **Harness x model** (`/evaluate/harness-by-model`): pick cases and
models (listed smallest first); the lab builds the grid on `harness_source` — no harness
(governance masked) versus this lab's harness — so the design is valid by construction.

## Consequences

- The lab can now test, on a team's own cases, whether the harness still pays on the
  model they use, and what each resolved task costs with and without it.
- Two harness levels × three models × three cases × three repetitions is 54 runs. The
  form states the count before launch; the cases, not the repetitions, are what narrow
  the interaction's interval.
- A third harness level (a short map between none and the full harness) needs a third
  `harness_source` value or a second axis; it is the natural next step and is not
  implemented here.

# ADR-0060: Compare can ask a model to read its own table

- **Status:** Accepted
- **Date:** 2026-09-11
- **Deciders:** Marcos Soto
- **Related:** [ADR-0053](0053-an-experiment-is-read-back-as-prose.md), [ADR-0033](0033-experiment-above-run.md)

## Context

`/advanced/compare` lines up two to four runs by hand and renders a metrics table —
case, models, prompt variant, max steps, ablation, objective, final score, composite,
tokens, cost, time, steps, interventions, plus every row the score registry holds for
those runs. It highlights the winning cell per row and warns, in an amber callout,
that no causal claim is implied.

What it never does is say what any of that means. A reader is left to work out for
themselves whether the second column actually improved on the first, which of the
em dashes mean *not measured* rather than *zero*, and which of the visible
differences between the two runs could even account for a delta. The page shows that
the prompt variant differs and the ablation differs; it never names that fact as the
thing the whole comparison hinges on.

The lab already knows how to answer that kind of question. ADR-0053 gave a finished
experiment an LLM reading: build a JSON evidence document out of what the database
already holds, hand it to an auditor model under a strict section contract, store the
Markdown and the evidence side by side, poll it from the UI.

Compare could not reuse that directly, because **a comparison has no server-side
identity**. An experiment is a row with an id. A comparison is a list of run ids
somebody assembled in a browser and will abandon in thirty seconds.

## Decision

Compare gets the same reading, keyed by the selection itself.

**1. The key is a hash of the run-id set.**
`comparison_key = sha256("\n".join(sorted(set(run_ids))))`. Sorted, so reordering the
columns does not orphan a reading that was already written for exactly those runs.
De-duplicated, so a selection cannot be keyed by an accident of repetition. The
`CompareAudit` row is unique on that key, and the service upserts: asking again
overwrites the reading in place rather than accumulating one row per press.

**2. Nothing generates on its own.**
The experiment reading fires automatically when the last run of an experiment lands
(`tasks.py`, on the run-completion path). Compare deliberately has no such hook. A
selection is transient and often idle — auto-reading every set of columns someone
scrolls past would spend gateway calls on questions nobody asked. An unread selection
returns 404 from the GET, and the panel renders that as an empty box. That 404 is the
normal state, not an error.

**3. The evidence names what the table cannot.**
Beyond the per-run rows the experiment auditor already builds (config, objective
summary, failed checks, judge rationale, registry scores, harness usage), the
comparison evidence carries two things the table shows but never states:

- `held_constant` / `differed` — which configuration fields are identical across the
  selection and which are not. "Did it improve" is only answerable against what
  changed.
- `deltas[]` against the leftmost run, each carrying `measured: false` when either
  side is null, and no `change` at all in that case. The page renders *not measured*
  and *zero* as the same em dash; the evidence must not.

Conditions are read from `RunConfig`, not by re-parsing `run.command` the way the
frontend's `parseFlags` has to.

**4. The report is written in English.**
The existing auditors — experiment, repository, and the file-mediated bench audits —
write Spanish, by a convention that predates this feature. This one does not. It
renders inside the web app, and the repository's rule is that user-facing UI copy is
English; a Spanish report would land Spanish prose on an English page. The
inconsistency is real and is left standing: translating the others is a separate
change with its own test surface — prompts, section headings, the tests that assert on
them, and the JSON fixtures under `tests/harness_lab_app/fixtures/` all move in
lockstep. A guard test asserts the new prompt is ASCII so the older convention cannot
drift back in by hand.

**5. The gate is the same one the experiment reading uses.**
Every selected run must be in a terminal state — including `awaiting_manual_score`,
which has a validation and a cost record and is what the table already renders. A
half-finished comparison would describe an outcome that does not exist yet and would
be persisted as though it did. The API answers 409 and names how many runs have
stopped.

## Consequences

- Compare gains one button and one stored row per distinct selection anyone chose to
  read. Selections nobody reads cost nothing.
- The reading is reproducible after the fact: the evidence the model was given is
  stored beside the prose, so any sentence can be checked against the numbers it came
  from.
- The lab now has two auditor languages. That is a known, deliberate, temporary split,
  recorded here. The fix is one translating pass over the older auditors, not new
  Spanish written to match them.
- A comparison of the *same runs in a different order* is the same comparison. If a
  future version of Compare gives column order meaning, the key has to change with it.

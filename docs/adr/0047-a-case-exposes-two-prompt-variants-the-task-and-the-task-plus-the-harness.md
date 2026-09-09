# ADR-0047: A case exposes two prompt variants — the task, and the task plus the harness

- **Status:** Accepted
- **Date:** 2026-09-08
- **Deciders:** Marcos Soto
- **Related:** [ADR-0028](0028-evaluation-modes.md),
  [ADR-0041](0041-a-skill-under-test-is-invoked-not-just-available.md),
  [ADR-0044](0044-a-single-prompt-case-can-carry-the-full-harness-pair.md),
  [ADR-0046](0046-an-experiment-reports-validity-before-it-reports-a-verdict.md),
  `benchmarks/tetris/task.yaml`, `scripts/build_prompt_variants.py`

## Context

`tetris-v1` shipped five hand-written prompts — `naive`, `incremental`,
`conductor`, `sdlc`, `sdlc_full` — offered to the operator as an undifferentiated
variant list. They do not vary the methodology. **They vary the task.**

| variant | what it actually asks for | states the graded contract? |
|---|---|---|
| `naive` | the whole game | no |
| `incremental` | one increment at a time | no |
| `conductor` | a planner/executor split | no |
| `sdlc` | **Milestone 1 only** — an empty 300×600 window | yes |
| `sdlc_full` | the whole game, 8 milestones | yes |

Two consequences followed, and both landed in the corpus.

**Pairing any two of them measures two factors.** The founding comparison
`naive` vs `sdlc` is not "harness off / harness on"; it is "build a Tetris"
versus "build an empty window and stop". Whatever delta it produced was
unattributable, and [ADR-0046](0046-an-experiment-reports-validity-before-it-reports-a-verdict.md)
recorded it as objection 4 after the fact rather than preventing it.

**Only two of the five state the contract every run is graded against.**
`validation_commands` runs `BENCH_SMOKE=1 SDL_VIDEODRIVER=dummy timeout 45
python {workspace}/main.py`, and the `BENCH_SMOKE` requirement appeared *only*
in `prompt_sdlc.md` and `prompt_sdlc_full.md`. Experiment
`does-the-full-harness-improve-outcomes-over-the-bare-model-on--2` ran `naive`
on both arms — the right single-factor design — and all four runs scored 1/10,
"files generated but does not run". They ran fine. Driven directly, all four
spawn pieces, apply gravity, move, rotate, lock into the board, clear a full
line and increment the score. Every one of them entered its interactive loop,
`timeout` killed it, `parse_objective` recorded `passed = exit_code == 0` as
false, and the LLM judge — which justified all four verdicts solely from "the
automated summary says 0/1 checks passed" — propagated the objective failure
into the subjective score. Four cells collapsed to a constant, and the arm
comparison had nothing left to compare.

A requirement the agent is never issued does not measure capability. It measures
whether the prompt happened to mention it.

The lab already had the right shape and did not apply it to its own founding
case. SWE-bench imports generate exactly two variants, and the treatment one is
built by concatenation:

```python
def _build_swebench_harness_prompt(spec):
    return _HARNESS_PREAMBLE + CaseService._build_swebench_prompt(spec)
```

The task body is byte-identical by construction. Nothing can drift.

## Decision

**A case exposes exactly two prompt variants.** `default` states the task, its
constraints and its acceptance criteria, and says nothing about the harness.
`harness` is `benchmarks/_harness_preamble.md` concatenated onto *the identical*
`default` body.

**The `harness` variant is generated, not written.**
`scripts/build_prompt_variants.py` (`make prompts`) rebuilds every
`prompt_harness.md` from its `prompt_default.md` sibling;
`tests/harness_lab_app/test_prompt_variants.py` asserts the identity, so a
hand-edit fails `make check` rather than silently becoming a second factor.
`make prompts-check` gives CI the same gate without writing.

**The graded contract belongs to the task, not to a variant.** Anything
`validation_commands` scores must be stated in `default`, so both arms are held
to a requirement both arms were issued. A test asserts that for `tetris-v1`
specifically, because that is the case where the omission cost four runs.

**The control prompt may not mention the harness.** A test rejects `.github/`,
`memory/context.md`, "skill" and "governance" in any `default` prompt. A control
that smuggles in harness instructions shrinks the gap the experiment exists to
measure.

**The prompt family is retired, not deleted.** The five legacy prompts move to
`benchmarks/tetris/legacy-prompts/` with a README explaining why. They are no
longer declared in `task.yaml`, so the wizard does not offer them. Historical
runs are unaffected: every run archives the prompt it was actually given at
`data/runs/<run_id>/prompt.md`.

**Re-seeding refreshes the variant map.** `seed_tetris_case` rebuilds
`prompt_variants` and `validation_commands` from `task.yaml` when the case
already exists — the same "re-import = refresh" rule
`import_swebench_pro_instance` follows — because the case is
`imported_from_yaml` and a stale database row keeps offering prompts the
benchmark no longer declares. Operator-owned fields (name, description, tags,
status) survive: those are what the case editor exists to change.

**The wizard prefers the new pairing.** `default`→`harness` is tried first,
then `swe_default`→`swe_harness`, then the legacy `naive`→`sdlc` for cases that
still ship a family.

## Consequences

The full-harness comparison becomes a single-factor comparison by construction
rather than by operator discipline. The three axes that separate the arms are
now the only things that differ, and each is a mechanism rather than a
paragraph someone wrote:

| axis | what it does |
|---|---|
| `skills_disabled` | removes skills from disk (rewrites the generated region of `CLAUDE.md`) |
| `skills_extra` | stages additional skills |
| `skills_invoke` | prepends the ADR-0041 activation block to every step's prompt |

This makes [ADR-0044](0044-a-single-prompt-case-can-carry-the-full-harness-pair.md)
the normal path instead of the accommodation it was written as. A case authored
in the Cases screen already defines one variant, `default`; it now needs nothing
further to carry the pair.

It does **not** fix the second half of the `--2` failure. The LLM judge still
derives its level almost entirely from the objective check, so one failed
command still collapses the subjective score to 1/10. That is a separate
decision about what the judge should read, and it is not made here.

The operator-facing simplification is the point. Choosing between five prompts
whose names do not describe how they differ is a decision that produces numbers
which look like results; choosing between "the task" and "the task plus the
harness" is not really a decision at all. The lab is meant to be handed to
people who are not its authors.

Both hand-authored cases now carry the pair. `cli-textstats` was migrated the
same way: its graded I/O contract — including the three exact stdout assertions
— moved into `prompt_default.md`, and the retired `naive`/`sdlc` pair went to
`legacy-prompts/`. That pair had the same defect in a milder form: `prompt_sdlc`
asked for a `tests/` deliverable the naive side never mentions and spelled the
contract out more precisely, so it handed the treatment arm an advantage
unrelated to the harness.

An already-imported case does not pick this up on its own: ADR-0025 makes the DB
the source of truth and `scripts/import_case.py` leaves an existing case alone.
It gained a `--refresh` flag that re-reads only the generated fields (`prompts`,
`validation_commands`) and leaves name, description, tags and status as the
operator left them. `tetris-v1` gets the same treatment through
`seed_tetris_case`, which now refreshes on re-seed instead of returning early.

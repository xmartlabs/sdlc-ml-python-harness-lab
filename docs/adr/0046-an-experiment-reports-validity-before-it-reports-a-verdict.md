# ADR-0046: An experiment reports validity before it reports a verdict

- **Status:** Accepted
- **Date:** 2026-09-08
- **Deciders:** Marcos Soto
- **Related:** [ADR-0006](0006-auto-incremental-run-ids.md),
  [ADR-0028](0028-evaluation-modes.md),
  [ADR-0034](0034-score-registry.md),
  [ADR-0038](0038-every-run-leaves-with-a-score.md),
  [ADR-0041](0041-a-skill-under-test-is-invoked-not-just-available.md),
  [ADR-0044](0044-a-single-prompt-case-can-carry-the-full-harness-pair.md)

## Context

Three `harness_evaluation` experiments were run to answer one question: *does the full
harness improve outcomes over the bare model on the Tetris case?* None of them could
have answered it. All three rendered a confident verdict anyway.

What the corpus in `data/app/harness_lab.db` actually holds:

1. **The treatment never happened.** All six full-harness runs carry
   `skill_invoked = 0.0`, reason `"none of <25 skills> invoked"`. The score existed, the
   evaluator fired, and `statistics.skill_usage_caveats` even wrote the correct sentence —
   but it landed as a footnote under a headline that read as a result. Worse, the summary
   never even reached that sentence: it rebuilt each arm's skill list from that arm's own
   `skills_extra`/`skills_invoke`, and a full-harness treatment stages nothing. The whole
   catalog is named only on the *control's* `skills_disabled`, so the treatment's skill
   list came back empty and the observation had nowhere to land.
2. **Infrastructure entered the corpus as capability.** Four of four opencode runs ended
   with `usage.json` `stop_reason: "harness_error"` (SSE timeout, connection reset) and
   were still recorded `state=completed, exit_code=0`, then auto-scored 0. A dead
   connection was averaged in as a model that could not solve the case.
3. **No headroom.** Two of the three had a control arm that passed every repetition. An
   improvement above 100% is unrepresentable; the design could not have detected the
   thing it was built to detect.
4. **Prompt and catalog confounded.** The wizard auto-pairs `naive`→`sdlc_full`, so two
   experiments varied the prompt *and* the skill catalog with no warning. That pairing is
   correct — it is what ADR-0044 decided `full_harness` means — but unattributable, and
   the reader was never told.

The common failure is not any one of these bugs. It is that **the lab had no way to say
"this comparison could not have measured the thing it names."** Everything it knew was
expressed as a caveat beneath a confident sentence, which is the one place a reader will
not weigh it.

## Decision

**An experiment reports a validity status before it reports a verdict.**
`statistics.assess_validity` returns `{"status": valid | degraded | invalid, "issues":
[...]}`, where each issue is a typed `ValidityIssue` — `code`, `severity`, `arm`,
`detail` (what happened, with counts), `remedy` (what to change and re-run). `summarize()`
returns it at top level next to `verdict`; the results page renders it in its own card
*above* the verdict card, never inside `verdict.caveats`.

Five codes, one proposition each:

| code | when |
|---|---|
| `treatment_not_activated` | an arm with skills opened none of them in any run — `invalid` on the treatment, `degraded` elsewhere |
| `infrastructure_failures` | `harness_error + failed` ≥ 50% of an arm's runs, or the arm has no scored runs at all — `invalid`; otherwise `degraded` |
| `no_headroom` | the control passed every scored run, so only "no change" or a regression was ever representable |
| `mixed_treatment` | an arm invoked its skills in some runs and not others |
| `design_objection` | a warning the designer was shown at creation and created the experiment anyway |

Modes that make no causal claim (`discovery`, `cross_repo`) are always `valid`.
`subject_validation` may carry `degraded` issues but never `invalid`: it has no control,
so headroom is meaningless there.

**An arm is about the skills its siblings remove.** `summarize()` now derives each arm's
skills the same way `score_service.build_context` did when it wrote the score: the arm's
own `skills_extra`/`skills_invoke`, plus every skill a sibling arm disables and this one
does not. Reading each arm in isolation is what silenced the observation on all six runs.

**The numbers are demoted, never withheld.** When `status == "invalid"`, `verdict` moves
its claim to a new `observed` field, replaces the headline with `"Comparison not valid"`,
and leads the caveats with the remedies. `highlights`, `movements` and `evidence` are
untouched. A new `tone` field (`invalid | better | worse | neutral`) replaces the
frontend's habit of sniffing the headline for "improvement"/"regression".

**A crashed harness exits non-zero.** `bench_run.main()` returns 2 on the managed path
when `stop_reason == "harness_error"` — *after* `write_usage_json` and after the workspace
is archived, because the evidence of why is the whole point. The worker reads the exit
code, so the run is marked `failed`, and objective validation and auto-scoring are skipped
because they sit behind `failure is None`.

**Design objections are persisted.** `Experiment.design_notes` (JSON) stores what the
designer was warned about at creation — both `validate_design`'s output and the wizard's
client-side advisories, posted on `POST /api/experiments`. `validate_design` also gained
the server-side mirror of the wizard's venue rule: inside the lab, every arm carries the
governance body of CLAUDE.md, the SessionStart hook and `.github/`, so a skills-only
factor there is never a bare-versus-governed comparison.

**The wizard defaults to asking.** `enforceSkills` now defaults `true` for `full_harness`
and `skills_extra` (the factors whose treatment is a skill the arm *has*), and stays off
for `skills_disabled`, whose treatment is removal. The advisory that used to fire only
when both arms shared a prompt now fires on the venue, which is what it was always about.

## Consequences

The three experiments above are re-read correctly with **no data migration**: validity is
computed on read, so the existing rows are flagged where they stand. Rewriting historical
`Run.state` would erase the record of what the lab believed at the time, and re-scoring is
a human decision.

This **extends ADR-0041 rather than reversing it.** 0041 explicitly rejected "refuse to
compute a verdict when the treatment arm never invoked its skill" — *the numbers are true,
and blocking hides them.* That still holds: nothing is withheld. What changes is
placement. The claim moves to `observed` and the validity status takes the headline, so
the reader meets the objection before the delta instead of after it.

It **refines ADR-0038.** 0038 said every run leaves with a score. It still does — a run
whose harness crashed leaves with its cost scores, because it spent tokens. What it no
longer leaves with is a *capability* score: a `stop_reason: harness_error` archived at
exit 0 was infrastructure entering the corpus as capability 0, and `scored_runs` now
excludes it so `success_rate` stops being denominated by dead connections.

The cost is a summary that can now be pessimistic about itself. An experiment with two
repetitions and a passing control will say so rather than reporting "no difference
observed", and some designs that used to render a green verdict will render a red card
instead. That is the intended trade: a comparison that could not have measured the thing
it names should be hard to mistake for one that did.

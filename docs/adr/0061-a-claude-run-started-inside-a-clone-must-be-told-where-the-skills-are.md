# ADR-0061: A claude run started inside a clone must be told where the skills are

- **Status:** Accepted
- **Date:** 2026-09-12
- **Deciders:** Marcos Soto
- **Related:** [ADR-0031](0031-harness-source-is-a-run-condition.md),
  [ADR-0041](0041-a-skill-under-test-is-invoked-not-just-available.md),
  [ADR-0049](0049-a-bare-arm-is-a-masked-runner-not-a-lab-root-run.md),
  [ADR-0051](0051-the-harness-ladder-is-one-family-of-single-factor-experiments.md),
  `memory/learnings.md` (2026-09-12)

## Context

An experiment meant to answer "is `systematic_debugging` worth having?" ran five
times against one SWE-bench instance under the `claude` harness with
`harness_source = lab_root`. Every run resolved the instance. The one arm asked to
invoke the skill recorded `skill_invoked = 1.0` and `skill_invocations = 5`; the two
arms asked to invoke twenty-three other skills invoked none. The lab's own
experiment audit marked the design degraded, and it was — but for reasons the audit
could not see.

The step records tell the whole story. All five `Skill` calls in the asked arm came
back `<tool_use_error>Unknown skill: systematic_debugging</tool_use_error>`. The
skill's text reached the agent once, in the last step, through `find` and a `Read`.
And the CLI's `system/init` event, identical across the runs, listed only Claude
Code's bundled skills: none of the lab's twenty-four governed skills was registered.

Two facts explain it, and both were already written down elsewhere.

1. The SWE lane's `swe_default` claude arm starts the agent **inside the workspace**
   — control-arm hygiene that predates harness conditions (ADR-0031, ADR-0049) —
   while `lab_root` promises that the agent sees the lab's harness at the repo root.
2. Claude Code loads project skills from `.claude/skills/` in the cwd *and its parents
   up to the nearest repository root*. A SWE-bench workspace is a clone: it is that
   root. The walk stops there and never reaches `/app/.claude/skills`. `CLAUDE.md`,
   by contrast, is collected from every ancestor — so the agent read a `CLAUDE.md`
   that named the skills as "projected into `.claude/skills/`", called them, and was
   told they did not exist.

Three defects compounded on top of the missing catalog:

- `trajectory.from_claude_events` and `attribution.parse_claude_events` counted a
  `Skill` call from its `tool_use` block alone, never checking the paired
  `tool_result` for `is_error`. Five rejected calls became "invoked, five times".
- The experiment wizard let an operator select twenty-three of twenty-four skills
  for a removal ablation. The one skill left unticked — the one meant to be tested
  — was thereby held constant: available to both arms, asked for by neither.
- The Compare page's ad-hoc reading, asked about one pair, credited a cost
  difference to `skills_invoke` in a pair where nothing had been invoked.

## Decision

**The runner hands the CLI a catalog of its own.** `bench_run.needs_staged_catalog`
is true whenever a `claude` run under `lab_root` starts the agent anywhere but the
lab root; `stage_skill_catalog` then copies the run's **effective** skill set into
`data/runs/<run>/harness_skills/.claude/skills/`, and `build_claude_cmd` passes that
directory as `--add-dir`. Claude Code loads `.claude/skills/` from an added
directory, so a directory whose only content is the catalog is exactly what it
needs. The set is the ablated one, resolved moments earlier, so an ablated arm stays
ablated; a masked condition (`bare`, `native`, `injected`) stages nothing, because
its lab views are empty stubs by design and an injected arm carries its skills
inside the workspace.

**The added directory is the catalog, never the lab root.** The first attempt named
`REPO_ROOT`. Claude Code indexes and watches an added directory, that one is 4.7 GB
across 141k files, and it *contains* `workspace/` — the clone the agent runs in — so
the add was recursive as well as vast. The CLI never reached session init: three
consecutive runs produced a zero-byte event stream and were killed by the stall
watchdog at ten minutes, `exit code 137`. A 352 KB staging directory reaches init in
ten seconds with all twenty-four governed skills registered. It is also the honest
surface: the agent gets the skills its condition grants and not read access to every
other run's artifacts.

**A rejected skill call is not an invocation.** Both event parsers first collect the
`tool_use_id`s whose `tool_result` carries `is_error`, then skip those calls when
counting invocations and consulted skills. The trajectory keeps the step (the agent
did reach for it, and a judge should see that) and tallies it in a new
`skill_call_errors` map. `SkillUsageEvaluator` folds that tally into the reason and
`provenance_detail.failed_calls` of `skill_invoked` and `skill_invocations`, so a
zero reads "none invoked; systematic_debugging: 5 call(s) failed — the harness
rejected the skill" rather than a bare zero. A call with no recorded result still
counts: a truncated record is not evidence of failure.

**The wizard names what a bundle selection holds constant.** With more than one skill
selected for `Remove skills` or `Add skills`, the design notes say the difference
belongs to the bundle, list the skills left out (when three or fewer), and say those
are available to both arms and tested by neither — the exact inversion this
experiment fell into.

## Consequences

- Every `lab_root` claude run that starts inside a clone now registers the lab's
  skills with the CLI. The SWE lane's `swe_default` arm keeps its cwd and its
  lab-root fingerprint (ADR-0049's "left broken on purpose" note about the cwd
  stands; this change supplies the catalog without moving the agent).
- Skill-activation scores recorded before this change for `claude` runs under
  `lab_root` with the agent in a clone are **not trustworthy**: the catalog was never
  loaded, and any `skill_invoked > 0` came from rejected calls or from a fallback
  `Read` of the file. Re-score such runs (`ScoreService.evaluate_run`) to get the
  corrected observation; re-run them to get a real one.
- The staging directory lives beside the run's other artifacts
  (`data/runs/<run>/harness_skills/`), so an archived run records the exact catalog
  its agent was offered — previously recoverable only by re-deriving the ablation.
- `skill_call_errors` is a trajectory field, not a registry score. It explains the
  two existing observations rather than adding a third, and it is stored with them
  in `provenance_detail`.
- The experiment audit still cannot recover an operator's intent. The wizard note is
  the only place the "all but one" inversion is caught, and it is an advisory, not a
  refusal: a twenty-three-skill bundle is a legitimate design when it is the one
  meant.

## Alternatives considered

- **Copy the effective skills into the workspace's `.claude/skills/`** (what
  `native` arms do for staged skills). Pollutes the workspace diff — the archive
  extracts `git diff` with intent-to-add — and rewrites a clone the run is supposed
  to leave untouched. Staging outside the workspace registers without writing into it.
- **`--add-dir <lab root>`.** Tried first and reverted: see the second decision
  above. An added directory is indexed and watched, so its size is not incidental.
- **Move the agent's cwd back to the lab root for the SWE lane.** Re-bins every
  historical SWE run against a fingerprint it never had (ADR-0049), and loses the
  control-arm hygiene the cwd exists for.
- **Drop rejected skill calls from the trajectory entirely.** Hides the agent's
  attempt from the judge and from the operator; the attempt is the diagnostic.
- **Refuse an "all but one" selection in the wizard.** Legitimate designs exist that
  remove nearly everything; the note tells the operator what the design measures and
  leaves the decision with them.

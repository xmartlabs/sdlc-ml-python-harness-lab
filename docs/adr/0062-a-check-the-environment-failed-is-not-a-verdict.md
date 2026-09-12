# ADR-0062: A check the environment failed is not a verdict

- **Status:** Accepted
- **Date:** 2026-09-12
- **Deciders:** Marcos Soto
- **Related:** [ADR-0029](0029-target-repositories-pinned-per-run.md),
  [ADR-0046](0046-an-experiment-reports-validity-before-it-reports-a-verdict.md),
  [ADR-0048](0048-a-check-that-did-not-measure-is-not-a-failure.md),
  [ADR-0057](0057-a-case-is-validated-before-it-can-measure-anything.md),
  [ADR-0061](0061-a-claude-run-started-inside-a-clone-must-be-told-where-the-skills-are.md)

## Context

The experiment `audit-ml-python-base` compared a target repository's own harness
(`native`) against the lab's harness projected into it (`injected`) — two cases, two
arms, two repetitions, claude/haiku. All eight runs recorded `0/2 checks passed`. The
validity engine reported `valid`, the verdict led with **"No difference observed"**,
and the LLM audit concluded that the harness "did not help and cost more".

None of that outcome was measured. The two commands that ran were the target's own
`validation_commands` — `make test` and `uv run pytest -x -q`, which replace the
case's by ADR-0029 — and they failed identically in every run for two reasons that
have nothing to do with any agent:

- Objective validation executes with `working_dir="/app"`, the lab root. A target's
  gate carries no `{workspace}` to anchor on (nor should it: the same string has to
  work in a gate check), so `make test` ran the **lab's** Makefile and died in its
  `format` target with `Error 127` — ruff is not on PATH in the hardened lane.
- The `validate` phase runs with `network_mode="none"` by design, so `uv` could not
  resolve its index and reported `Temporary failure in name resolution`.

Both were stored as `conclusive: true` failures, so each run scored
`task_validation_pass = false`, every arm sat at 0 % success, `success_delta` returned
exactly `0.0` points, and nothing in `assess_validity` had anything to say: the
ceiling rule (`no_headroom`) fires on a control at 100 %, and its mirror did not
exist. Re-running the *cases'* own `{workspace}`-anchored checks by hand against the
archived workspaces gave native 4/4 and injected 3/4 — the result the lab should have
produced.

Gate checks never had the first problem: `build_gate_check_spec` prefixes every
command with `cd {workspace} && `, and `SubprocessRunExecutor` even documents the
assumption that commands carry their own `cd`. Objective validation never got the
same line.

## Decision

**A target's gate runs inside the workspace.** When `build_validation_spec` is given
`target_commands`, it renders `{workspace}` as `.` and prefixes each command with
`cd <workspace> && `, mirroring `gate_check_service.build_gate_check_spec`. The spec
also carries `cwd`, so a run whose checks all failed can say from where. Case commands
are untouched: they anchor on `{workspace}` themselves (ADR-0057 refuses one that does
not), and some also use lab-relative paths that a `cd` would break.

**Two more classes of check settle nothing.** `classify_inconclusive(exit_code,
output)` replaces the bare exit-code set and returns a reason:

| reason | when |
|---|---|
| `not_launched` | exit `-1` — the process never started |
| `timeout` | exit `124` |
| `killed` | exit `137` / `143` |
| `not_found` | exit `127`, or output matching `command not found` / `: not found` at end of line / `Error 127` |
| `no_network` | output matching the resolver failures a `network_mode="none"` sandbox produces |

`127` counts on the exit code alone because the dry-run executor supplies no output;
the reason is named `not_found` rather than "missing tool" because an unanchored path
produces the same code, and the label must not assert more than the data supports.
The output patterns are consulted only on a non-zero exit, and `not found` is anchored
to end of line so ordinary test prose cannot match. `No such file or directory` is
deliberately *not* a reason: that is usually the agent not having written the file,
which is the whole point of the check.

Every check now carries `inconclusive_reason` beside `conclusive`. The summary names
the reasons observed instead of listing every reason the lab knows, and when *every*
check was inconclusive it stops leading with a score at all: `0/2 checks passed` is
literally true and reads as a verdict, which is the sentence this whole distinction
exists to stop. `describe_objective_for_judge` renders `[INCONCLUSIVE: no network]`.

**Every lane that runs a repository's own commands uses the same classifier.**
`dry_run_verdict`, `gate_check_service.summarize` and `health_at_pin` all read it, so
a dry run, a gate check and a real run cannot disagree about one command. In
particular `health_at_pin` returns **`unmeasured`** — not `fail` — when the validate
command could not run itself. It reported `fail` for `ml-python-base`, whose
`make test` re-runs `make install`, and the audit printed *"Suite al pin: **roja**"*:
a claim about someone else's repository that the evidence did not support. The
auditor model caught it in prose and corrected it; a heuristic must not be less
careful than the text it feeds. The label for `unmeasured` is now *"no se pudo
medir"*, and `CASE_PROPOSALS_PROMPT` distinguishes the two states — a red suite
cannot measure the agent, an unmeasured one is not known to be red at all, and only
the second calls for fixing the measurement environment.

The two auditors keep `failed_checks` but filter it to conclusive failures and serve
the rest as `inconclusive_checks` with their reason.

**The suggestions are network-aware, and the report says the constraint out loud.**
`infer_commands` proposes `uv run --offline pytest -x -q` rather than plain `uv run`,
which re-resolves the lock and dies on DNS in a lane that has no network while losing
nothing — the setup phase already built the venv. A new readiness check, **Offline
validation**, names any configured validation command that will reach the network,
resolving a task-runner target against the Makefile the report already read so that
`make test` whose recipe calls `make install` is judged by what it does rather than
waved through for what it is called. It stays silent about a target whose body it
could not read: silence beats a warning the lab cannot support.

**The floor is detected like the ceiling.** Two new validity issues, both computed on
read:

- `floor_effect` — no arm with at least `EXPLORATORY_ONLY` scored runs resolved
  anything. `INVALID` when every scored run of every arm failed the *same* check
  (the environment decided, not the agents) or when the smallest arm has
  `STRONGER_EVIDENCE` runs; `DEGRADED` otherwise, because two failing runs are
  evidence of two runs. Mutually exclusive with `no_headroom` by construction.
- `objective_unmeasured` — every arm has runs, no scored run, at least one run whose
  checks all settled nothing, and every run accounted for as unmeasured or unhealthy.
  Always `INVALID`. Gated on evidence rather than absence so an experiment polled
  while its runs are still queued does not flash "Comparison not valid".

**A tie on success is reported as a tie on success.** `_compose_verdict` printed
"No difference observed" whenever the success delta was zero, which is a sentence
about one dimension applied to the whole comparison — it appeared over a pair whose
cost, tokens, steps and wall time had each moved by about a third in the treatment's
favour. When success ties, the headline now reads the efficiency highlights and says
**Same outcome, lower cost** / *higher cost* / *mixed efficiency*, keeping
**No difference observed** for the case where nothing moved. Materiality is a 10%
relative move, a floor on the wording in the spirit of `_MATERIAL_POINTS` and never a
significance claim; the outcome metrics (`success_rate`, `test_pass_rate`) are
excluded, since the question is what the result cost. When the arms stopped under
different rules the headline says the cost moved and declines to credit a direction —
that delta partly measures the stop criterion, which the caveat already states.

Comparison is made on a `objective_signature(objective, run_id)` that replaces the run
id with a placeholder — every workspace path contains it, so raw command strings from
two runs would never match — and keeps inconclusive checks out of the failure tuple.
`assess_validity` gains `arms` and `check_signatures`; both issues run for
`harness_evaluation` and `subject_validation`, which keeps its `DEGRADED` cap.

## Consequences

- Every target-paired run validated the lab until now. Their stored objectives are
  wrong and stay wrong: validation writes at validation time, so a run has to be
  **re-validated** for the new classification and the new cwd to apply. This is the
  one asymmetry with ADR-0046's compute-on-read stance, and it is deliberate — the
  alternative is rewriting history the lab recorded in good faith.
- An inconclusive objective no longer drags a run's composite down (`compute_composite`
  caps only on `passed is False`), and a SWE-bench run whose harness itself hit a
  missing binary now falls through to the judge or to manual scoring instead of
  auto-scoring zero.
- The dry run gets stricter in the right direction: a check whose command does not
  exist used to exit 127 on the clean tree and read as `ok` — "the case has something
  to measure". It is now `inconclusive`.
- A gate check can now end `unmeasured` where it used to end `fail`, so a repository
  is no longer told its suite is red on evidence that only says the lab could not run
  it. `ready` is unaffected: it still asks whether a run would execute and be scored.
- The offline check is a warning, never a gate. A repository whose gate genuinely
  needs the network is still registrable; what changes is that the operator reads it
  at registration rather than discovering it in an experiment's validity card.
- `run_health` gains `unmeasured`, and the experiment summary gains two issue codes.
  The frontend's issue-code union and `VALIDITY_TITLE` map grow with them.
- Still out of scope: the `validation_network` policy itself. `network_mode="none"` on
  the validate lane stays; the fix is to stop reading its consequences as the agent's.

## Alternatives considered

- **Reject a target's `validation_commands` at registration unless anchored.**
  `readiness_service.infer_commands` legitimately emits bare `make test`, and the same
  string has to keep working in a gate check, where it already runs from inside the
  checkout. Anchoring is the caller's job, not the author's.
- **Add 127 to `INCONCLUSIVE_EXIT_CODES` and stop there.** Misses the case that
  actually happened: make exits 2 when a recipe's binary is missing, and `uv` exits 2
  when DNS fails. The exit code alone can never catch either.
- **Treat `No such file or directory` as infrastructure.** It is the commonest way a
  real agent failure surfaces; classifying it would silence the check that matters.
- **Fold the floor into `verdict.caveats`.** ADR-0046 settled this: a caveat printed
  under a confident headline is the failure the validity card exists to fix.
- **Give the validate lane a network so a repository's own gate just works.** That
  network is the point: it is what stops audited code from calling out while it is
  being measured. The gate belongs offline; the commands have to suit it.
- **Refuse a validation command that needs the network.** Some repositories cannot be
  tested any other way, and refusing would make them unregistrable rather than
  honestly described. A warning at registration puts the choice where it belongs.

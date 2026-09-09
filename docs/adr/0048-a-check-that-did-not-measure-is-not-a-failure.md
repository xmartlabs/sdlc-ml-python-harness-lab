# ADR-0048: A check that did not measure is not a failure

- **Status:** Accepted
- **Date:** 2026-09-08
- **Deciders:** Marcos Soto
- **Related:** [ADR-0028](0028-evaluation-modes.md),
  [ADR-0034](0034-score-registry.md),
  [ADR-0038](0038-every-run-leaves-with-a-score.md),
  [ADR-0046](0046-an-experiment-reports-validity-before-it-reports-a-verdict.md),
  [ADR-0047](0047-a-case-exposes-two-prompt-variants-the-task-and-the-task-plus-the-harness.md)

## Context

Four Tetris runs — every one of them a working game, with a board, a falling piece,
line clears and a score — were all recorded 1/10. The chain that produced that number
had nothing to do with the code the agents wrote:

1. The task prompt never stated the `BENCH_SMOKE` contract, so the agents wrote an
   interactive `main.py` (correct for "build Tetris", wrong for a validated case).
   ADR-0047 fixes that half: the graded contract now lives in `prompt_default.md`.
2. The validation command was `timeout 45 python main.py`. Plain `timeout` sends
   SIGTERM; an SDL program turns SIGTERM into an `SDL_QUIT` *event* and keeps running.
   The command wedged and exited 124 — or, once the drain loop was blocked on
   `iter_lines()`, never returned at all.
3. `passed = exit_code == 0` read 124 as "the code fails".
4. `evaluation.test_pass_rate` reported `"0/1 checks passed"` and
   `tasks._autoscore_run` handed the judge that one line as its objective evidence.
   A judge that can read the diff was told, in effect, that the diff does not run.

Every stage was individually defensible and the composition was a lie. The lab
converted its own inability to observe into a statement about the agent — exactly the
failure mode ADR-0046 named as *infrastructure entering the corpus as capability*,
one layer further down: not a crashed harness, a check that never concluded.

## Decision

**A check carries three verdicts, not two.** `validation.parse_objective` writes
`conclusive: bool` on every check: `False` when the exit code is in
`INCONCLUSIVE_EXIT_CODES = {-1, 124, 137, 143}` — timed out, killed, or never
launched. The objective verdict becomes three-way: `False` if any *conclusive* check
failed (a real failure stands on its own and is not softened by a sibling timeout),
`None` if none failed but some did not conclude, `True` otherwise. The summary line
says so: `"0/1 checks passed; 1 inconclusive (timed out, killed, or never launched)"`.

**A verdict that did not measure does not enter the corpus as a zero.**
`evaluation.test_pass_rate` is computed over conclusive checks only, and reports how
many were excluded; with nothing conclusive it emits no score at all rather than 0.0.
`task_validation_pass` already skipped on `passed is None`. So an inconclusive run
lands where ADR-0038 wants it — with its cost scores, without a capability score —
instead of averaging a dead command in as a model that could not solve the case.

**The timeout is made to actually kill.** `_portable_timeout` rewrites
`timeout <N>` to `timeout -k 5 <N>`, so a process that swallows SIGTERM gets SIGKILL
five seconds later. That closes the SDL case but not the general one — the wedge can
be anywhere, and `RunHandle.wait()` takes no deadline while `iter_lines()` is the
blocking call. `validation_service._drain` therefore arms a `threading.Timer`
watchdog for `command_deadline_seconds(command)` (the command's own budget plus the
kill grace plus a 30s margin, else a 900s default) that calls `handle.stop()`, and
records the result as exit 124 with `"validation deadline exceeded"`. Nothing in
validation can now block the worker forever.

**The judge gets the checks, not the headline.** `describe_objective_for_judge`
renders one line per check — `[PASS|FAIL|INCONCLUSIVE] exit=N <command>` plus clipped
detail — and `tasks._autoscore_run` passes that instead of the summary string. The
rubric system prompt now distinguishes the two: *a FAIL is a fact about the code and
refutes every level it contradicts; an INCONCLUSIVE check is a fact about the lab and
refutes nothing, so where the checks are inconclusive the diff is the evidence —
award the levels it demonstrably implements, withhold any level only a passing check
could establish.*

**The UI stops drawing "did not measure" in red.** A check with
`conclusive === false` renders `⊘` in the warning tone, and the run badge reads
`objective: inconclusive` rather than `objective: manual` when checks exist.

## Consequences

The objective check keeps its authority. This ADR deliberately does *not* move the
"does it work" verdict to the LLM judge, even though the judge already reads the diff
(`judge.py:build_rubric_message`). Deriving the pass/fail from generated artifacts
would make the headline number a function of the judge model, inject judge variance
into the very delta an experiment measures — fatal at two repetitions per arm — and
break comparability with every run already scored. What changes is narrower and
sufficient: the checks remain the ceiling for "it works", and the diff is allowed to
carry the lower rubric levels precisely when the checks abstain.

Historical rows have no `conclusive` key. Both the evaluator
(`check.get("conclusive") is not False`) and the frontend (`conclusive?: boolean`)
treat its absence as conclusive, so nothing is re-read or migrated — consistent with
ADR-0046's choice to compute validity on read rather than rewrite what the lab
believed at the time. The four Tetris runs stay at 1/10 in the database; re-running
them under the fixed prompt and the fixed timeout is a human decision.

The cost is a lab that can now return "I don't know". A wedged environment that used
to yield a clean 0.0 yields an absent score and a warning badge instead, and an
experiment missing scores may trip ADR-0046's `infrastructure_failures` issue and
report itself degraded. That is the intended trade: an unmeasured case should be hard
to mistake for a measured failure.

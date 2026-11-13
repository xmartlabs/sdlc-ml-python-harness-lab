# Per-step reward: which step did the work, and what can the harness lose?

> **Inspired by reinforcement learning.** In RL an agent acts in steps and each step
> earns a reward; learning means crediting the right steps. The lab borrows that
> view to *measure*, not to train: every step of a coding agent is graded with the
> case's own checks, so you see where the progress came from — and what the run
> spent after the work was already done. Decision record:
> [ADR-0076](adr/0076-per-step-reward.md).

## Why a developer should care — in two minutes

Most agent benchmarks grade the end state: *did it pass, how many steps, how much
did it cost?* That hides the most useful fact about a run. Here are three real
`tetris-v2` runs (Claude Opus, ten graded tests), regraded after every step:

| Run | Harness | Tests after step 1 | Steps run | Spend after the tests already passed |
|---|---|---|---|---|
| K-019 | lab root, plain prompt | 10/10 | 4 | **74%** |
| K-020 | bare | 10/10 | 5 | **71%** |
| K-034 | lab root + harness prompt | 10/10 | 2 | 32% |

Every run solved the task in its first step. The rest of the spend went to
re-listing files, writing extra tests, adding features nobody asked for, and — in
two runs — reading the benchmark's own `scoring.md`. In RL terms the agent went
looking for the reward function.

The end-of-run scores rate all three runs as equal successes. The per-step view
shows that **most of what two of them cost bought nothing**, and it puts a number
on it that you can track release after release. That is the continuous-improvement
metric: *cost to resolve*, and *share spent after resolution*, going down as the
harness and the stop criterion get better.

## The idea

A run is an **episode**. Each harness invocation is a **step**. The **reward signal**
is the case's own objective checks — the same commands that decide `test_pass_rate`.

```
progress_t = conclusive checks passing after step t  /  conclusive checks
reward_t   = progress_t − progress_{t−1}                 (progress_0 = 0)
```

The rewards **telescope**: they sum to the final progress, so the per-step view
never contradicts the run's verdict — it explains where the verdict came from.

Cost is kept as its own series rather than folded in as `reward − λ·cost`. The lab
does not choose a price for a token on your behalf; the summary metrics below are
the trade-off a λ would make, stated without one.

## What it measures

Five scores, all **facts** (the case's commands exited 0 or not), registered in
[scores.py](../src/harness_lab_app/scores.py) under `source: step_reward`:

| Score | Better | Meaning |
|---|---|---|
| `steps_to_resolve` | lower | First step after which every conclusive check passed. Absent if never. |
| `cost_to_resolve_usd` | lower | Spend through that step: the price of the solution, not of the stop rule. |
| `post_resolution_cost_share` | lower | Share of spend after resolution. Uses billed tokens when the model was free. |
| `progress_auc` | higher | Mean progress over the steps. 1.0 = solved in step one and kept. |
| `step_regressions` | lower | Steps after which fewer checks passed than before. |

`cost_to_resolve_usd` is the one to compare arms on. When both arms pass every
check, the success rate cannot tell them apart — this still can.

## How it is measured

1. **The runner photographs the workspace after every step.** A build-from-scratch
   workspace is copied to `data/runs/<id>/steps/step-N.snapshot/` (installed
   directories like `.venv` and `node_modules` excluded). A cloned repository is
   recorded as `steps/step-N.diff` against the commit that was HEAD when the step
   ended (kept as `base` in the meta, because HEAD moves if the agent commits). Each
   step also writes `steps/step-N.meta.json` (snapshot kind, step clock, base, and on
   the SWE lane the live F2P/P2P verdict). The diff is taken on a throwaway copy of
   git's index, so the agent's own staged changes are never touched between its
   steps — and the copy keeps the index's mtime, without which git trusts stale
   stat data and a same-size edit made in the index's second drops out of the diff.
2. **The worker grades every snapshot after the run**, with the case's validation
   commands, the same executor and the same parser as the final validation
   ([ADR-0075](adr/0075-every-arm-is-measured-by-the-same-instrument.md)). A
   command that does not name the workspace (a tamper guard on the lab's own git
   state) is left out of the per-step pass: it would grade the final state and
   credit it to every step. The SWE lane needs no re-run: the runner already
   measured F2P/P2P live, and progress there is the share of FAIL_TO_PASS fixed,
   scaled by the share of PASS_TO_PASS kept — an untouched issue is 0, not 18/19.
   **SWE-bench Pro** is the exception: its suite runs in the instance's docker
   image and the runner has no docker, so the live verdict is skipped. The worker,
   which has the socket, rebuilds each step's tree — a self-contained local clone
   of the archived repository, checked out at the step's `base`, with the step's
   diff applied — and grades it with the case's own validator (`--repo-dir`). A
   `git worktree` does not work here: its `.git` file points at the archive by a
   worker path that does not exist in the grading container, and suites that list
   files through git (`ansible-test`) never start. A suite that did not run prints
   a placeholder `F2P=0/n` beside a `SWEBENCH-ERROR:` line; that step is
   **unmeasured**, never read as "no test passes".
3. **Each step's cost** comes from its own record: claude's per-step `result`
   events. Usually one per step, but a session that answers again (a background
   task finishing after the final message) emits another, and the fields add up
   differently: `usage` and `num_turns` cover that one response and are summed,
   while `total_cost_usd` is the session's running total, so the last value is the
   cost. opencode reports cost per run, so its steps have no cost yet; progress and
   regressions are still measured.
4. The trajectory is stored on the run's validation (`objective.per_step`), scored
   into the registry, and shown in the run's **Per-step reward** panel. A
   re-score also **removes** any `step_reward` score the new trajectory no longer
   supports — a run that stops resolving after a re-validation loses its
   `steps_to_resolve` instead of keeping the old one.

**The agent never sees its reward.** Grading happens after the run, in the worker.
Running the checks inside the loop would let the agent read its score and change
what it does — the measurement would alter what it measures, and the reward could
be gamed. (The SWE lane is the one exception that predates this: it has always
checked F2P/P2P between steps to stop the loop.)

## From "when" to "why": credit, then ablation

Each step also records what it opened from the harness — skills (`skill:`) and
governance documents (`doc:`) — whether through a Read tool, a `Skill` call, or a
shell read such as `cat .github/standards.md` (matched against the run's own
governance surface; a heuristic, since a shell command is text, not a typed
event). The panel lists, for each item, the reward earned by
the steps that consulted it. Across the corpus, `GET /api/scores/step-credit` ranks
every skill in the catalog by that evidence, never-opened skills first.

That ranking is **co-occurrence, not cause**. A skill read in the step that solved
the task may have done nothing; the agent might have solved it anyway. Only an
ablation answers *would it have happened without this?*:

```bash
make prune MODEL=claude-haiku-4-5-20251001 \
  CASES="tetris-v2 cli-textstats-v1 clinic-slots-t1" TOP=4 REPS=3 LAUNCH=1
```

This creates one experiment: a control arm with the full harness, and one
treatment arm per skill, each removing exactly that skill. With no `ITEMS`, it
tries the four skills the corpus credits least. Its summary carries a `pruning`
block with one verdict per skill:

- **removable** — every paired interval stays inside the margin: success loses at
  most 5 points, and `cost_to_resolve_usd` / `progress_auc` move at most 20% of the
  control.
- **keep** — an interval sits wholly past a margin: removing the skill made the
  agent fail more, resolve later, or pay more.
- **inconclusive** — an interval straddles the margin, fewer than three cases
  were paired, or a required metric was never measured on a paired case (runs
  from before per-step snapshots carry success but no `cost_to_resolve_usd` or
  `progress_auc`). Success alone never makes a skill removable.

"No difference observed" is never enough to remove anything. With few runs almost
nothing shows a difference, so that rule would empty the harness one
under-powered experiment at a time. The rule is non-inferiority: removal must be
*shown* harmless within a margin declared up front
([pruning.py](../src/harness_lab_app/pruning.py)).

**Harness value depends on the model**
([ADR-0066](adr/0066-harness-and-model-vary-together-only-in-a-full-grid.md)). A
skill that is dead weight for Opus can carry a 27B local model. Run one pruning
family per model tier, and remove a skill only for the tiers where it is
removable.

## The improvement loop

```
run cases ──► per-step reward ──► corpus credit ──► make prune ──► remove / keep
   ▲                                                                    │
   └──────────── cost_to_resolve and post-resolution share fall ◄───────┘
```

Each turn of the loop gives the team one sentence it can defend: *"Removing
`brainstorm_quick` left success and cost-to-resolve within margin on Haiku over
three cases; we removed it, and the template is 1.8k tokens lighter."*

## Limits, stated

- **Cloned repositories** (SWE-bench, target repos) are snapshotted as diffs. The
  SWE lane is graded from its live verdict, and SWE-bench Pro is regraded in the
  worker from each step's diff; a target repository's per-step diffs are recorded
  but not yet regraded, so those steps show as unmeasured. A run recorded before
  diffs carried their `base` cannot be regraded until it is filled in.
- **A run with no gradable step** says so ("none of N step(s) could be graded")
  rather than "never fully resolved" — the gap is the grader's, not the agent's.
- **opencode** has no per-step cost yet; its curve has progress but no cost share.
- **A snapshot over 50 MB** is recorded as `too_large`, never silently skipped.
- **Runs from before this change** have no snapshots and no per-step reward. They
  keep every end-of-run score they had.
- **Grading cost.** A case with ten checks and five steps runs fifty checks after
  the run. Snapshots are graded only when the run finished cleanly.

## Where to look

| What | Where |
|---|---|
| A run's curve | Run detail → **Per-step reward**, or `GET /api/runs/{id}/step-reward` |
| Pruning shortlist | `GET /api/scores/step-credit` |
| Pruning verdicts | Experiment summary → `pruning` (`GET /api/experiments/{id}/summary`) |
| The math | [step_reward.py](../src/harness_lab_app/step_reward.py), [pruning.py](../src/harness_lab_app/pruning.py) |
| The decision | [ADR-0076](adr/0076-per-step-reward.md) |

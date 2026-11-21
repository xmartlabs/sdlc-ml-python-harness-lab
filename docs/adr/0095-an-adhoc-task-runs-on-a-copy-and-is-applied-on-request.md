# ADR-0095: An ad hoc task runs on a copy and is applied on request

- **Status:** Accepted
- **Date:** 2026-11-21
- **Deciders:** Marcos Soto
- **Amends:** [ADR-0042](0042-the-lab-reads-a-repository-through-the-engineers-own-checkout.md),
  [ADR-0045](0045-a-checkout-the-lab-can-be-given-not-only-watched.md)
- **Related:** [ADR-0029](0029-target-repositories-pinned-per-run.md),
  [ADR-0030](0030-one-workspace-source-interface.md),
  [ADR-0031](0031-harness-source-is-a-run-condition.md),
  [ADR-0094](0094-codex-and-gemini-run-on-their-own-subscription-login.md),
  plan [2026-11-21-adhoc-local-tasks](../plans/2026-11-21-adhoc-local-tasks.md),
  guide [guide-adhoc-tasks.md](../guide-adhoc-tasks.md)

## Context

Everything the lab measured so far was a benchmark: a frozen case, a grader, a
score. The question engineers ask day to day is different: *this* change, in
*my* repository, with this harness and this model, how much did it cost and was
it any good? Answering it needs four things the lab lacked:

1. a run that starts from a free prompt against a folder on the machine, not
   from a case;
2. a reviewable change that can go back into that folder, which ADR-0042 and
   ADR-0045 forbade ("the lab never writes to the operator's working copy");
3. the cost of the run kept after the run, so the same task can be repeated
   with another model and compared;
4. a disk budget: a run directory holds a full copy of the repository it ran
   in (318 MB for one ansible run; 5.7 GB across the lab on 2026-11-21), and
   real repositories make that worse with every run.

## Decision

**An ad hoc task is a pinned copy of the operator's folder plus a free prompt;
each run works on that copy, and its change reaches the folder only when the
operator presses Apply or runs the printed command.**

1. **Reuse, not a second engine.** A task is a `TargetRepository` whose
   checkout is the lab's own clone (`data/checkouts/adhoc-<slug>-NNN`), pinned at
   the operator's HEAD, and a synthetic `Case` of type `adhoc` carrying the
   prompt. Runs go through the ordinary pipeline with `harness_source=native`:
   preflight, provisioning, runner, progress, cancellation and cost capture are
   unchanged. Every run of a task starts from the same commit, which is what
   makes "same task, another model" a comparison.
2. **The clean patch.** After the run the worker writes `change.patch`: a
   `git diff --binary` of the archived copy against the lab's provisioning
   commit (so setup output and anything the lab staged stay out), with
   dependency and cache directories excluded. It applies on the operator's
   base commit.
3. **Apply amends ADR-0042/0045.** The rule now reads: *the lab writes to the
   operator's folder only when they press Apply (or Undo) for one reviewed
   change, only the files that change touches, never inside `.git`, and never
   a commit, a branch, the index or a stash.* The API keeps its read-only
   mount. The worker launches a one-shot container (worker image, no network)
   that mounts exactly that folder read-write plus the patch read-only, runs
   `adhoc_git.py` as the folder's owner, and removes itself. Preconditions are
   checked inside, immediately before writing: no merge/rebase/cherry-pick in
   progress, no `index.lock`, no uncommitted change in a file the patch
   touches, and `git apply --check`. `git apply` is all or nothing, so a refused
   apply leaves the folder as it was. Undo is `git apply --reverse` under the
   same checks, refused once the applied files changed. One apply per folder
   at a time: the `AdhocRun.apply_state` row plus a Redis `SET NX` lease (file
   locks exclude nothing on the Docker Desktop bind). The printed command
   (`git apply --3way …`) stays available for anyone who prefers their own git.
4. **Measurement outlives the files.** `AdhocRun` holds tokens (input, output,
   cache, reasoning, billed), real or estimated cost and its source, minutes,
   turns, tool calls, files and lines changed, the decision (approved /
   rejected / discarded, with a reason) and the apply state. Ad hoc runs are
   left out of the leaderboard, the run list, the case library, the targets
   list and the attention feed: one-off work is not an attempt at a benchmark.
5. **Least data for ad hoc runs.** The archived copy is deleted as soon as the
   patch exists; `change.patch` and a few small logs stay until the run is
   decided, or for `HARNESS_LAB_ADHOC_RETENTION_DAYS` (default 7). Rejected or
   discarded runs lose their files immediately. An applied change keeps its
   patch so Undo works. The DB rows always stay.
6. **Light mode for benchmark runs.** A Storage page reports disk use by
   category and offers purges: expired ad hoc files, all ad hoc files, sandbox
   copies, and the workspace copies of finished benchmark runs older than N
   days. A light-purged run keeps diff, logs, steps, usage and scores and is
   marked `purged_at`; it can no longer open a sandbox, be re-validated or be
   continued. Purging is a button, never a schedule, for benchmark runs.

## Consequences

- The lab can measure real development: acceptance rate, cost per accepted
  change, and iterations to acceptance by harness and model, with a local model
  at $0 in money and its tokens and time recorded.
- The operator's uncommitted work is not in the copy (only commits are). The UI
  warns when the folder is dirty; including uncommitted changes is deferred.
- The folder must be under `HARNESS_LAB_LOCAL_SOURCES_DIR` and shared with
  Docker Desktop, the same requirement the read-only picker already had. The
  worker now receives `HARNESS_LAB_HOST_LOCAL_SOURCES_ROOT` to name it to the
  host daemon; it does not mount it.
- Sending a private repository to a cloud model is now one click away. The UI
  states the destination for each model; local models keep it on the machine.
- A task's runs are anecdotes one by one; aggregates across tasks are where
  the signal is. Tasks never enter experiments or the leaderboard.
- `create_all` registers only imported models; the app imports `models` before
  `init_db`, so the two new tables are created on start-up like every other.

## Alternatives considered

- **Hand the operator a branch to fetch instead of writing files.** Zero write
  risk, but a branch needs a commit, and a commit needs an author: the lab
  would either impersonate the operator or author code itself, which the
  authorship rule forbids. Kept as the printed `git apply` command instead.
- **Mount the local sources root read-write in the API.** Simplest, and it
  would let any bug in any endpoint write to every repository on the machine.
  One folder, one container, one action keeps the blast radius to the change
  the operator approved.
- **A separate execution engine for ad hoc runs.** Would duplicate preflight,
  provisioning, streaming and cost capture, and drift from them. The synthetic
  case + target costs one enum value and an id prefix.
- **Keep ad hoc runs whole, like benchmark runs.** Nothing reads the archived
  copy after the patch is written, and on real repositories it is the bulk of
  the disk.

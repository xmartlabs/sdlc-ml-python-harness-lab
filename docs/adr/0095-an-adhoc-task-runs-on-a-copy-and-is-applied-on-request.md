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
2. **The clean patch, built where the agent ran.** The runner records the
   provisioning commit's SHA (`provisioned_commit.txt`) before the agent starts
   and, when the run ends, writes `change.patch` inside its own container: a
   `git diff --binary` against that SHA (so setup output and anything the lab
   staged stay out, and an agent that amends the commit cannot hide its work),
   with dependency and cache directories excluded, kept as **bytes** (CRLF,
   Latin-1 and binary hunks survive). It is built before the lab's validation,
   so nothing validation writes can reach the operator's repository. Git runs
   on the agent's copy **hardened**: no system/global config, `core.fsmonitor`,
   hooks, attributes, external diff and textconv off, every configured filter
   driver overridden, and a config `include` refused — the agent wrote that
   `.git/config`, and nothing in it may run in the lab's processes.
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
   apply leaves the folder as it was. Undo is `git apply --reverse`, refused
   when any touched file's SHA-256 differs from what apply recorded — `git
   apply -R --check` alone would pass an edit far from the agent's hunks and
   silently take part of the file back. One apply per folder at a time: the
   `AdhocRun.apply_state` row plus a Redis `SET NX` lease that outlasts the
   container timeout (file locks exclude nothing on the Docker Desktop bind).
   The task is not acknowledged late and compare-and-sets the row before
   writing, so a redelivered message never writes twice; an apply that finds its
   change already in place reports it applied. The printed command
   (`git apply --3way …`) stays available for anyone who prefers their own git.
4. **Measurement outlives the files.** `AdhocRun` holds tokens (input, output,
   cache, reasoning, billed), real or estimated cost and its source, minutes,
   turns, tool calls, files and lines changed, the decision (approved /
   rejected / discarded, with a reason) and the apply state. Ad hoc runs are
   left out of the leaderboard, the run list, the case library, the targets
   list and the attention feed: one-off work is not an attempt at a benchmark.
5. **Files while you review, none after.** An undecided or approved run keeps
   its whole directory — the workspace (sandbox: terminal, desktop), `steps/`
   (which skills the agent opened), the diff and the logs (LLM audit, LLM
   judge) — for `HARNESS_LAB_ADHOC_RETENTION_DAYS` (default 7). Rejected or
   discarded runs lose their files immediately; an applied run past retention
   keeps only `change.patch`, so Undo still works. The metrics row and the
   patch's file list are in the database from the moment the run ends.
   *Amended the same day:* the first version deleted the workspace as soon as
   the patch existed, which left the sandbox, the audit and the skills panel
   with nothing to read. Whole tasks are the operator's to prune: a task can be
   marked kept (`AdhocTask.keep`), and "delete unkept" removes every other
   task with its copy, runs and metrics. It skips, and names, any task with a
   run going or a change applied, since deleting that one would lose Undo.
6. **Evaluation on request.** The case-specific checks do not apply to a free
   prompt, so an ad hoc case carries a generic rubric — "does the change do
   what the task asked, without unrelated edits" — and the LLM judge grades
   against it when the operator presses the button (one model call, never
   automatic). The LLM audit and the skills panel are the ones every run has.
7. **The agent's harness is a choice per run.** `repo` (the default, and every
   run before this choice existed) is `harness_source=native`: the agent sees
   only what the operator's repository ships. `minimal` and `full` are
   `harness_source=injected` at that skill profile (ADR-0085): the lab's
   harness projected into the copy. One staged skill can be tried on top of
   any level (`skills_extra`), and optionally required (`skills_invoke`,
   ADR-0041). The level is stored on `AdhocRun.harness_level`, so the table
   compares the same model with and without a harness. None of it reaches the
   operator's folder: every projection — the injected harness and the staged
   skill added to a `native` arm alike — is placed **before** the provisioning
   commit, the runner records the paths it wrote (`harness_paths.json`,
   including `*.native.bak` backups of a displaced `CLAUDE.md`), and
   `change.patch` excludes them by literal path, so an agent's edit to a
   projected skill or adapter file stays in the copy too. Two bugs surfaced on
   the way, both fixed for every in-target run, not only ad hoc ones: the staged
   skill of a `native` arm was copied *after* the baseline commit, so the
   judge read it as the agent's work; and `metadata.json` listed the lab's
   skills and subagents for a `native` run that could see none of them (the
   catalog is now read from the agent's workspace), which made the audit
   grade six subagents "no evidence" on a repository with no harness.
8. **Light mode for benchmark runs.** A Storage page reports disk use by
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

## Verification

Exercised end to end on 2026-11-21 against the running stack with a real model
(Haiku 4.5, $0.06): task creation copied and pinned a throwaway repository, the
runner built a 250-byte patch, metrics were recorded, Apply wrote the file
unstaged and owned by the operator's uid through the one-shot container, and
Undo left the folder clean. The first live run found a bug no unit test had:
`git apply --numstat` run from `data/runs/<id>/` — a subfolder of the lab's own
repository — reads paths relative to that subfolder and drops them all, so the
file list came back empty (and the dirty-overlap check went blind). The count
now runs in an empty directory outside any repository.

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

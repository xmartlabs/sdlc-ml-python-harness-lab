# Ad hoc tasks — run a real task on your own repository

Use the lab for everyday work, not only benchmarks. Pick a folder on this
machine, write what you want done, run it with any harness and model, review
the change, and apply it to your folder with one button — or copy a command and
apply it yourself. Every run is measured, so you can run the same task again
with another model and compare what each cost.

Decision record: [ADR-0095](adr/0095-an-adhoc-task-runs-on-a-copy-and-is-applied-on-request.md).

## Before you start

- The folder must be a git repository with at least one commit.
- It must sit under the local sources root: `HARNESS_LAB_LOCAL_SOURCES_DIR` in
  `.env` (default: the folder the lab itself lives in). Change it and restart
  with `make app-up`.
- Docker Desktop must share that folder (Settings → Resources → File sharing).
  The picker reads it read-only; **Apply** mounts the one folder read-write for
  a few seconds.
- After pulling this change, run `make app-up` (or `docker compose up -d`) so
  the API and workers reload and the worker gets the new environment variable.

## The flow

1. **Ad hoc tasks → New task.** Browse to the folder. The lab shows its branch,
   HEAD and any uncommitted files.
   - **Uncommitted changes are not copied.** The agent works on your last
     commit. Commit or stash first if the task depends on them.
2. **Write the prompt.** One thing per task, the acceptance criterion, and the
   files involved if you know them. Optional: setup commands (run in the copy
   before the agent, e.g. `uv sync`) and verification commands (your tests).
3. **Create task.** The lab copies the folder into
   `data/checkouts/adhoc-<name>-NNN` and pins your HEAD. Your folder is not
   touched.
4. **Run with…** a harness, model and effort. The panel says where your code
   goes: a cloud model sends it to that provider; an `lmstudio/` or `ollama/`
   model keeps it on this machine.
5. **Review.** When the run ends you get the changed files with flags worth a
   second look (lockfile, CI, deleted, binary, secret-like, large) and the diff.
   Then choose:
   - **Apply to my repo** — writes the change into your folder as uncommitted
     edits. Nothing is committed, staged or branched; you commit it yourself.
     It is refused, with the reason, when you have uncommitted changes in a
     file it touches, a merge or rebase in progress, or the change no longer
     fits your files. A refused apply writes nothing.
   - **Undo** — reverses an applied change while those files are unchanged.
   - **Copy command** — `git -C <folder> apply --3way <patch>` for your own
     terminal; `--3way` lets you resolve a conflict the button refuses.
   - **Approve without applying**, **Reject** (with a reason) or **Discard**.
6. **Run again with another model.** Every run of a task starts from the same
   commit, so the table compares like with like: tokens, cost (real, estimated
   for subscriptions, $0 for local models), minutes, files and lines changed,
   decision, apply state.

## What is kept, and for how long

| | Kept |
|---|---|
| Always (database) | prompt, harness, model, effort, tokens, cost, time, turns, files/lines changed, decision and reason, apply state |
| Until decided, or 7 days | `change.patch` and a few small logs in `data/runs/<run>/` |
| Never past the run | the copy of your repository the agent worked in |
| Applied changes | the patch stays (a few KB) so Undo works |

Rejected and discarded runs lose their files immediately. Change the retention
with `HARNESS_LAB_ADHOC_RETENTION_DAYS`. Deleting a task removes its copy, its
runs and its metrics.

## Storage page

**System → Storage** shows disk use by category and offers purges:

- **Expired ad hoc files** and **all ad hoc files** — metrics stay.
- **Sandbox copies** of stopped sandboxes.
- **Benchmark workspaces** ("light mode") — deletes the workspace copy of
  finished benchmark runs older than N days. Diff, logs, steps, usage and
  scores stay; those runs can no longer open a sandbox, be re-validated or be
  continued. The run is marked `purged_at`.

## What can go wrong

| Symptom | Cause | Fix |
|---|---|---|
| "is outside the local sources root" | the folder is not under `HARNESS_LAB_LOCAL_SOURCES_DIR` | point the variable at a parent folder and restart |
| Apply fails with "could not mount your folder" | Docker Desktop does not share it | add it under File sharing |
| "uncommitted changes in files this change touches" | your work in progress overlaps the change | commit or stash, then apply |
| "does not fit your current files" | you changed the same lines since the task was created | use the copy command (`--3way`) and resolve, or create a new task on your new HEAD |
| "Interrupted by a worker restart" | the stack restarted during an apply | check `git status` in the folder before trying again |
| The run did not see my latest edit | only commits are copied | commit, then create a new task |

## Measuring with it

One run of one task is an anecdote. The useful numbers are aggregates over many
tasks: acceptance rate, cost per accepted change, and runs until acceptance, by
harness and model. Ad hoc runs never enter the leaderboard or experiments.
Aggregate by harness and model, never by person.

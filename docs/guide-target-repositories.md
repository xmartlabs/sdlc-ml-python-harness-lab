# Running a case against another repository

By default a run works in the lab's own workspace. A **target repository** lets the
same case run inside a different repo — your team's service, a client's codebase, an
open-source project — cloned fresh for every run and pinned to an exact commit.

> Looking for the end-to-end walkthrough — register a repo, derive a case from its
> history, run `native`, compare against `injected`, record findings? That is
> [Auditing someone else's repository](guide-web-ui.md#auditing-someone-elses-repository--worked-example)
> in the Web UI guide. This page is the reference for the repository side of it.

This is what makes two questions answerable:

- *Does our harness work outside the repo it was designed in?* — same case, several
  repositories.
- *What happens when an agent works in **their** repo?* — the first half of an audit.

Decision record: [ADR-0029](adr/0029-target-repositories-pinned-per-run.md).
The provisioning contract: [ADR-0030](adr/0030-one-workspace-source-interface.md).

## 1. Register the repository

**Repositories** in the sidebar → *Add a repository*.

| Field | What it is |
|---|---|
| **Id** | Short slug used everywhere (`company-payments`). |
| **Repository URL** | `https://…`, `ssh://…`, `git://…` or `user@host:path`. |
| **Default ref** | The branch/tag a run pins when it names no ref of its own. |
| **Setup commands** | Run after checkout, before the agent (`uv sync`). One per line. |
| **Validation commands** | The repo's own gates (`make test`). One per line. |

Press **Check** on the row to see the commit the default ref points at right now. A
private repo with no credentials, or a typo'd branch, fails here — with git's own
message — instead of after a container has started.

### Credentials

The clone happens inside the run container. An `https://` URL to a public repo needs
nothing. A private repo needs credentials reachable from that container: either a
token embedded in the URL, or an SSH key mounted into the runner image. The lab does
not manage repository credentials for you, and `GIT_TERMINAL_PROMPT=0` means a
missing credential fails fast rather than hanging.

## 2. Point a case at it

In the run form, pick the repository under **Target repository** and optionally
override the **Ref**. **Check commit** shows the exact SHA the run will be pinned to.

The case supplies the task; the repository supplies the code. They are paired at the
run, not baked into each other — which is what lets one case run against many
repositories.

> A case only means something in a repository where its task makes sense. Running
> `build a Tetris clone` against a payments API measures nothing. Cases derived from
> the target's own history are a separate piece of work — see the
> [v2 roadmap](roadmap-lab-v2.md#phase-3--case-derivation-from-a-target).

## 3. Choose which harness the agent sees

With a repository selected, the **Harness** field picks the condition
([ADR-0031](adr/0031-harness-source-is-a-run-condition.md)):

| Condition | Where the agent runs | What it sees |
|---|---|---|
| **Lab harness** | the lab repo | the lab's harness; edits the target from outside |
| **Native** | inside the target | only what that repository ships |
| **Injected** | inside the target | the lab's harness projected into it |

**`native` vs `injected` is the comparison.** Same repository, same commit, same
case, same model — the harness is the only thing that changes. That is the question
an external engagement actually starts from:

> What does our harness add over how this team already works?

Do **not** compare `lab_root` against `native`: those differ in the harness *and* in
whether the agent is working inside the repo at all. Two changes, one number.

### What injection does

The lab's skills, agents, adapter file (`CLAUDE.md` / `AGENTS.md` / `OPENCODE.md`)
and governance documents are **copied** into the clone before the baseline commit —
so they never show up in the agent's own diff — and they carry the run's ablation, so
an ablated arm stays ablated inside the target.

If the repository already ships governance of its own, injection displaces it and
keeps it as `<name>.native.bak`, recording the path in the run's
`harness_projection.collisions`. Replacing a team's own `CLAUDE.md` changes the thing
being measured, so it is never silent.

### Why the two arms cannot collide

A cloned repository has no `skills-lock.json`, so fingerprinting both arms against
the lab would give them the **same** `harness_hash` — one `condition_hash`, one arm,
and a comparison that quietly stopped comparing. For `native` and `injected` the
fingerprint is taken from the *workspace*: the native skill directories, projected
agents, adapter files and governance docs the agent can actually read from its cwd.

## 4. Compare across repositories

Turn on **Batch mode** and select several repositories under **Target
repositories**, and both conditions under **Harness**. The batch expands into the
cross-product: 2 repositories × 2 harness conditions × 5 repetitions is 20 runs, with
every arm's commit resolved once so the repetitions really are repetitions.

**Read those results per repository, never pooled.** A mean across three different
codebases is not a measurement of anything: the repositories are not equivalent
conditions. What the comparison legitimately shows is *where the harness holds and
where it degrades*. This is Mode B in [ADR-0028](adr/0028-evaluation-modes.md).

## What the run records

Every run against a target stores the resolved commit, and the commit — not the ref —
is part of its `condition_hash`:

```json
{
  "target_repo_id": "company-payments",
  "target_commit": "6147ce…",
  "harness_source": "injected",
  "harness_projection": {
    "tools": ["claude"],
    "skills": ["brainstorm_quick", "…"],
    "agents": 6,
    "collisions": ["CLAUDE.md"]
  },
  "target_repository": {
    "target_id": "company-payments",
    "repo_url": "https://github.com/company/payments-api.git",
    "ref": "main",
    "commit": "6147ce…"
  }
}
```

Two consequences worth knowing:

**Two commits of one repository are two arms, not repetitions of one.** If you queue
five runs against `main` and someone pushes in between, the lab would otherwise
record five runs of "main" that were really two experiments. The batch resolves the
ref **once** and pins every generated run to that commit.

**"Run again" does not follow the branch.** Cloning a run reuses its recorded commit,
because a clone is the same arm and a re-resolved `main` would quietly be a different
one. To measure a newer commit, create a new run.

## How a workspace is built

```text
resolve ref → immutable SHA        (at run creation, in the API)
        ↓
clone the repository at that SHA   (in the run container)
        ↓
run the setup commands
        ↓
commit the provisioned state       (so `git diff HEAD` is only the agent's work)
        ↓
agent runs
        ↓
validation — the repository's gates, not the case's
```

Build artifacts a repo already ignores (`.venv`, `node_modules`) stay ignored: the
provisioning commit honors `.gitignore`, and an ignored file never appears in the
diff.

If a setup command fails, the run stops there. Starting an agent against a
half-installed repository produces a failure that looks like the agent's fault and
is not.

## Validation precedence

When a run has a target, the **repository's** validation commands replace the
case's. A repo knows how it is tested better than a portable case does, and a case
that runs across several repositories cannot name each one's test command. A target
with no validation commands leaves the case in charge.

## Limitations

- **The audit still reads the lab's rules**, not the target's. This matters most for
  a `native` run, where the governance under evaluation is *theirs* — the auditor is
  reading the wrong document. Tracked as
  [Phase 5](roadmap-lab-v2.md#phase-5--evaluation-schema).
- **Injection is backup-and-replace, not merge.** A merged `CLAUDE.md` would be a
  third artifact that is neither arm.
- **A third-party harness repository is not supported.** The two sources are the
  lab's own and the one already inside the target.
- **Deleting a target archives it.** Historical runs keep a resolvable id rather
  than pointing at nothing.

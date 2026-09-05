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

**Evaluate → Audit a repository** (the front page's third door) walks the three
steps: register the repository, clone it yourself with the command the page shows
(into `data/checkouts/<id>`, where your ssh agent and tokens already work), and
pin a commit ([ADR-0042](adr/0042-the-lab-reads-a-repository-through-the-engineers-own-checkout.md)).
**Evidence → Repositories** lists what is registered, offers *Add a repository*
for the form alone, and **Open** leads to the repository's workbench
(`/evidence/repositories/<id>`).

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

### What the runner can execute

Setup and validation commands run **inside the runner image**, not on your
machine. The image ships git, Python 3.11 (`uv`, `pip`), and Node 20 with
`npm`, `pnpm` and `yarn` baked in via corepack. A repository whose gate needs
anything else — Go, Rust, a system dependency — needs that added to
`Dockerfile.runner` first, or its setup fails fast with the tool's own
"not found" before any agent time is spent (by design: the run stops rather
than measuring an agent against a half-built repository).

### Credentials — you clone it, the lab reads your clone

The lab never authenticates to a git remote on your behalf, and it does not want your
credentials (ADR-0042). Instead you clone the repository yourself:

1. Open the repository under **Repositories** (or start the guided flow at
   **Audit a repository**). It shows the exact command, with the destination already
   filled in:

   ```
   git clone git@github.com:company/payments.git /path/to/lab/data/checkouts/company-payments
   ```

2. Paste it into your own terminal. Your ssh agent, your tokens, your VPN — whatever
   already works for you works here, because it *is* your shell.
3. The page detects the clone within a few seconds and shows its HEAD. Press **Pin this
   commit**.

From then on every run, readiness report and derived case reads that local checkout at
that commit. No container ever contacts the remote, so a private repository needs no
special setup at all.

Two consequences worth knowing:

- **Uncommitted changes are invisible to runs.** Provisioning clones git objects, so
  your working-tree edits never reach an agent. The page warns you when the tree is
  dirty rather than blocking — it is your working copy, not the lab's.
- **The pin does not move on its own.** `git pull` in your terminal changes nothing
  until you press **Re-pin to HEAD**. That is the point: two experiments a week apart
  compare agents, not a moving branch.

A repository registered before this existed keeps working over the network — the lab
falls back to the remote URL whenever there is no local checkout.

### Does the repository pass its own gate?

Before spending model budget, press **Run gate check** on the readiness step. It
provisions the pinned commit in the runner image, runs the repository's setup and its
own validation commands, and stops — no agent, no prompt, no model.

It exists because a failed run against an unfamiliar repository is ambiguous: the agent
may have failed, or the repository may simply not build in the runner image, and those
look identical in the output while meaning opposite things. The gate check answers that
once, cheaply, and names the phase that failed (provision / setup / validate).

## 2. Point a case at it

In **Evaluate → New evaluation**, pick the repository under **Repository**; the run
is provisioned from the commit pinned on your local checkout. In the single-run form
(**Advanced → Runs**), pick it under **Target repository**, optionally override the
**Ref**, and **Check commit** shows the exact SHA the run will be pinned to.

The case supplies the task; the repository supplies the code. They are paired at the
run, not baked into each other — which is what lets one case run against many
repositories.

> A case only means something in a repository where its task makes sense. Running
> `build a Tetris clone` against a payments API measures nothing. Cases can be
> derived from the target's own history instead: the **From history** tab of the
> Audit wizard, or **Derive cases** on the Repositories page (both call
> `POST /api/targets/{id}/derive-cases`), mine the checkout's commits for
> candidate tasks, which you review and save as cases
> ([ADR-0032](adr/0032-cases-are-derived-from-a-targets-own-history.md)).

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

- **The audit judges each run against the governance it actually received**: a
  `native` run against the target's own rules, an `injected` run against the lab
  harness ([ADR-0036](adr/0036-audit-judges-a-run-against-its-own-governance.md)).
  Audits recorded before that change say so instead of showing a surface they
  never had.
- **Injection is backup-and-replace, not merge.** A merged `CLAUDE.md` would be a
  third artifact that is neither arm.
- **A third-party harness repository is not supported.** The two sources are the
  lab's own and the one already inside the target.
- **Deleting a target archives it.** Historical runs keep a resolvable id rather
  than pointing at nothing.

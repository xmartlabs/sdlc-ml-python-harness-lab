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

**Evaluate → Audit a repository** (the front page's third door) walks four steps:
register the repository, put a checkout under `data/checkouts/` (clone it yourself
where your ssh agent and tokens already work, attach one that is already there, or
have the lab copy a folder you have on this machine) and pin a commit
([ADR-0042](adr/0042-the-lab-reads-a-repository-through-the-engineers-own-checkout.md),
[ADR-0045](adr/0045-a-checkout-the-lab-can-be-given-not-only-watched.md)); then the
harness audit, which decides from the tree whether the lab can go on
([ADR-0054](adr/0054-a-repository-is-audited-before-the-lab-runs-anything-on-it.md));
then a case; then an experiment.
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

### The repository is usually already on your machine — point at the folder

Step 1 of **Audit a repository** opens a folder picker on the directory the lab sits in,
before it asks for anything else. Browse to the repository, press **Audit this folder**,
and that one press registers it, copies it in and pins it.

The picker reads as a file browser rather than a form:

- **Where you are** — a breadcrumb from the shared root down to the current folder, every
  ancestor clickable, plus **Up one level**. At the root it says *Top of the shared
  folder* rather than showing a greyed-out button.
- **What is in front of you** — one row per folder. A git repository is drawn differently
  from a plain folder in icon, badge and wording, never in colour alone.
- **What to press** — **Audit this folder** sits on each repository row, and again on the
  folder you are standing in when that one is a repository. A folder that is not a
  repository says so and tells you to open one of the folders below.

You do not type a URL: the folder knows its own `origin` and its current branch, and the
lab reads both off it. The repository id is the folder's name. The one folder that still
needs a form is one that was never pushed anywhere — with no `origin` there is no URL to
record, so use the second panel, *Or register one the lab has to clone*.

- The copy is `git clone --no-hardlinks`: local, no network, no credentials, and your
  working copy is only ever read. `--no-hardlinks` matters — without it git would link
  the copy's objects into your own repository.
- The mount the picker reads is **read-only**. The lab cannot write into your working
  copy even if it wanted to.
- **It is frozen at the commit it was on.** Commits you make afterwards do not reach the
  lab. To move it, come back to step 1 and pick the folder again.
- The copy exists because a run container mounts only the lab's own folder: a repository
  living anywhere else is invisible to every run
  ([ADR-0045](adr/0045-a-checkout-the-lab-can-be-given-not-only-watched.md)).

The picker shows the directory it is browsing. If your repositories live elsewhere,
point `HARNESS_LAB_LOCAL_SOURCES_DIR` at the directory that holds them and restart the
lab (`make app-down && make app-up`):

```
HARNESS_LAB_LOCAL_SOURCES_DIR=/Users/you/code
```

Nothing outside that root is reachable, and symlinks pointing out of it are refused.

**Keep that root a directory of repositories — never your home directory.** Docker
Desktop injects filesystem events for every path it shares, and a home directory
(`~/Library`, caches, synced folders) produces enough of them to time that service out
and stop the engine: `service fs failed: injecting event blocked for 60s`.

### Credentials — or clone it yourself, and the lab reads your clone

For a repository that is *not* on this machine yet, the lab still never authenticates to
a git remote on your behalf, and it does not want your credentials (ADR-0042). You clone
it yourself:

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

#### A clone that is already under data/checkouts

The third tab, **A clone I already have**, lists every git repository already under
`data/checkouts/`, whatever its directory is called, and attaches the one you pick.
Costs nothing and copies nothing. One clone may serve several repositories — the list
says which already use it — so auditing the same codebase twice needs one checkout, not
two. Attaching does not pin: press **Pin this commit** as usual.

The copy command is also always written out for your own terminal, whatever the picker
can see, so nothing here depends on the mount.

Two consequences worth knowing:

- **Uncommitted changes are invisible to runs.** Provisioning clones git objects, so
  your working-tree edits never reach an agent. The page warns you when the tree is
  dirty rather than blocking — it is your working copy, not the lab's.
- **The pin does not move on its own.** `git pull` in your terminal changes nothing
  until you press **Re-pin to HEAD**. That is the point: two experiments a week apart
  compare agents, not a moving branch.

- **Copied checkouts carry commits, not your desk.** `git clone` reads the committed
  tree at the ref, so uncommitted files stay where they are.

A repository registered before this existed keeps working over the network — the lab
falls back to the remote URL whenever there is no local checkout.

If step 1 waits without finding anything, it says so after about fifteen seconds: which
path it is watching, whether that directory exists, whether it is a repository, and
which other sources are available. It keeps watching — a large clone legitimately takes
minutes.

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

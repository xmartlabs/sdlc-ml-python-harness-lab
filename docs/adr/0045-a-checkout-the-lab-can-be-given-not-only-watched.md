# ADR-0045: A checkout the lab can be given, not only watched

- **Status:** Accepted
- **Date:** 2026-09-07
- **Deciders:** Marcos Soto
- **Amends:** [ADR-0042](0042-the-lab-reads-a-repository-through-the-engineers-own-checkout.md)
- **Related:** [ADR-0029](0029-target-repositories-pinned-per-run.md),
  [ADR-0019](0019-two-ways-to-run-the-lab.md)

## Context

ADR-0042 gave the audit flow exactly one path: the engineer clones the repository in
their own terminal into `data/checkouts/<target_id>`, and the lab watches that
directory. It solved the credential problem and it still does. But it assumed the
repository is not on the machine yet, and for the people using the lab it usually is —
these are the repositories they work in.

Two failures follow, both observed on a real machine.

**A clone that already exists is invisible.** The lab watches one directory whose name
it derives from the target id. A repository cloned under any other name — an older
target id, a typo, or one clone that two targets both want to audit — cannot be used at
all. The only remedy the flow offered was a second full copy of a client's source under
the right name.

**The wait never ends and never explains itself.** With no clone at the watched path,
step 1 polls forever behind a spinner labelled *"Watching for the clone"*, saying
neither which path it watches nor what it found there. On the machine that prompted
this there were two targets for the same repository, one of them pointing at a
directory that was never going to appear.

The obvious fix — "let me point at the folder I already have" — is not available as
stated. The API container mounts only the lab root (`.:/app`), and so does the runner
container, so a path anywhere else on the host is invisible both to the API and to
every run and gate check. Referencing it would need a per-target bind mount in the
runner. The checkout has to live under `data/checkouts/`.

## Decision

**The lab still never writes to the engineer's working copy, but it may create a
checkout of its own under `data/checkouts/` when explicitly asked.**

1. Decision 1 of ADR-0042 is amended. It read "the lab never clones". It now reads:
   the lab never writes to the operator's own repository, and creates a checkout only
   under `data/checkouts/`, only on an explicit request, and only by *reading* a source.
2. Step 1 offers three sources, all ending in the same place. **The folder picker
   leads**: the repository being audited is normally already on the machine, so the
   first thing the step asks is which folder it is in, not which remote to clone.
   - **Pick a folder on this machine** — `git clone --no-hardlinks <source> <dest>`
     performed by the lab, followed immediately by the pin. One press, because "use
     this folder" is one decision and a copy nobody pinned is a checkout the rest of
     the audit refuses to read. The same command is always available for the
     operator's own terminal.
   - **Attach a clone that is already there** — `GET /api/checkouts` lists every git
     repository under `data/checkouts/`, whatever its directory is named, and attaching
     records it as the target's `checkout_path`. Attaching is not pinning: the clone
     was not made by this step, so the commit stays a separate act.
   - **Clone from the remote** — the guided command of ADR-0042, unchanged, for a
     repository that is not on the machine yet.
3. **Decision 3 of ADR-0042 stands unchanged**: a local clone source is accepted only
   under `data/checkouts/`, and a target id is one validated path segment. That fence
   is what makes everything above safe, so nothing here widens it — a recorded
   `checkout_path` outside the fence is ignored rather than honoured, and the target
   reads as "not cloned yet".
4. Reading a checkout stops assuming the directory name. `resolve_checkout_path(target)`
   is the single source of truth — the attached path when there is one, the
   `data/checkouts/<target_id>` convention otherwise — and inspection, ref resolution
   and repository context all go through it. `checkout_path_for(target_id)` remains, but
   only to name the *destination* of a new clone.
5. The folder picker is **on by default, rooted at the directory the lab sits in**.
   `${HARNESS_LAB_LOCAL_SOURCES_DIR:-..}` bind-mounts one directory **read-only** at
   `/host-sources`; `local_sources_service.browse` lists one level under it, resolving
   every path before checking that it is still inside the root, so a symlink cannot
   escape. The variable **moves** that root, it is not a switch: repositories kept
   somewhere other than beside the lab need one line in `.env` and a restart.

   Two earlier revisions were wrong in opposite directions. The first left the picker
   off until the variable was set, which made the leading path invisible to the person
   it was written for. The second defaulted the root to `$HOME` so that nothing had to
   be configured — and took Docker Desktop down with it: the engine injects filesystem
   events for every shared path, and a home directory (`~/Library`, caches, synced
   folders) produces enough to time that service out
   (`service fs failed: injecting event blocked for 60s`), killing the whole lab. **A
   shared root is a working set, not a search space.** The default has to be a
   directory small enough to watch, which is why it is the lab's own neighbourhood
   rather than everything the operator owns.
7. **Picking the folder is the whole of step 1** — it registers the repository too.
   The form asking for an id, a name and a repository URL used to come first, which
   put a GitHub URL between the operator and a repository already sitting on their
   disk. The picked folder answers all three itself: `browse` reads its `origin` and
   its current branch with read-only git, the id is slugged from the folder name (with
   a numeric suffix when that id is taken), and one press registers, copies and pins.
   A folder with no `origin` is the one case that still needs typing — the lab records
   a URL for every repository and will not invent one — and the form below is where
   that is done.
8. **The freeze is stated where the choice is made.** The picker says, before the
   press, that the folder is copied and frozen at its current commit and that later
   commits do not reach the lab until the folder is picked again. A pin the operator
   did not know they were making is the same surprise as a moving branch.
9. A wait that finds nothing **says what it is watching**. After ~15 seconds step 1
   names the watched host path, whether the directory exists and whether it is a
   repository, and points at the other two sources. The polling continues: the operator
   may simply be cloning something large.

## Consequences

**A repository already on the machine is usable in seconds**, without a second copy of
client source and without the network. Attaching costs no disk at all; copying costs one
local object-store copy and no credentials.

**One clone can serve several targets.** The list shows which targets already claim a
checkout, and sharing is allowed — auditing the same repository twice is a normal thing
to want, and it was previously the case the flow handled worst.

**`--no-hardlinks` is not an optimisation to remove.** Git would otherwise link the new
checkout's objects into the operator's own repository, which is precisely the coupling
ADR-0042 exists to prevent: the lab must not be able to affect their working copy.

**The copy carries commits, not the desk.** A copied checkout has the committed tree at
the cloned ref; uncommitted files stay where they are. This matches what a run sees
anyway (provisioning clones git objects), and the UI says so.

**A dead wait is now diagnosable but still a wait.** Stopping the poll after N attempts
was considered and rejected: a large clone legitimately takes minutes, and a stopped
poller that the operator has to restart is a worse failure than a wait that explains
itself.

**The lab can list part of the host filesystem** — a capability it did not have before.
The lab is a single-operator tool bound to loopback on the operator's own machine, and
the root is one directory of their own repositories, mounted **read-only**, resolved
before it is checked, capped in the number of entries it returns, and reachable only by
the API (the worker, which launches containers, never sees it). Keeping that root small
is a Docker constraint before it is a security one, and the two happen to want the same
thing. The copy command in the operator's own terminal loses no capability either way.

**Repositories outside the root need one line of configuration.** That is the price of
not sharing a home directory, and it is paid once: the picker prints the root it is
browsing, and `HARNESS_LAB_LOCAL_SOURCES_DIR` plus a restart moves it.

**The engineer can still do all of it from their terminal.** Every server-side action
has a command equivalent the page will write out, so a deployment that does not want the
mount loses no capability.

# Learnings

> Non-obvious facts discovered while working: gotchas, why-it-is-this-way, dead ends
> to avoid. Append new entries at the top. One fact per entry.

## 2026-11-13 — The case assistant still drafted the cases ADR-0071 forbids

- **A rule written for `task.yaml` did not reach the drafter.** ADR-0071 banned grading
  with the agent's own tests and required declared grader dependencies, but the case
  assistant's prompt still advised `pytest {workspace}/test_*.py`, and no draft, form or
  editor could carry `validation_requirements`. When a rule changes how cases are graded,
  check `case_draft_service._SYSTEM_PROMPT` too (ADR-0077).
- **"Inconclusive: grader dependency missing" on a non-SWE case is the runner, not the
  worker.** Those checks run in the runner image (it has `pytest-cov`, no numpy). Reproduce
  with `docker run --entrypoint sh harness-lab-runner:latest`, not `docker compose exec
  worker`, which has neither and gives a misleading `--cov-report` error from the lab's
  own `pyproject.toml`.
- **`str.format` on a shell command breaks on its own braces.** `d={'a': 1}` in an inline
  check passed case validation and then raised `KeyError` in every run. Render with
  `validation.render_command`, which fills only `{workspace}`/`{run_id}`.
- **`update_case` wiped `metadata_json`.** The editor sends no metadata; assigning every
  payload field set it to `{}`. Metadata is merged key by key now.

## 2026-11-13 — Four traps found by the first real runs of the per-step reward

**`claude --add-dir <dir> <prompt>` eats the prompt.** `--add-dir` is variadic, so
the prompt became another directory and the CLI exited "Input must be provided …
when using --print" before any model call (ansible-0ea K-001). Latent since 2026-09-23
but only reached by claude + `lab_root` + an agent cwd inside a clone (the staged
skill catalog); ansible-0ea was the first run to hit it. Use `--add-dir=<dir>`.

**`total_cost_usd` is the session's running total; `usage` is per-response.** A
background task finishing after the agent's final message makes the session emit a
second `result`. Summing costs reported eda-ml-iris K-002 at $0.83 for $0.49. Cost
= last value per session; tokens/turns/duration = sum.

**A copy of git's index must keep its mtime** (`copy2`, not `copyfile`). git flags a
"racy" entry (edited in the index's second, same size) only by comparing with the
index's mtime; a copy stamped now makes git trust the stale stat and the edit
vanishes from the step diff. Intermittent — reproduce by forcing the times.

**A tree graded in another container must be a self-contained clone.** A `git
worktree`'s `.git` file (and `--shared`'s alternates) point at the archive by a
worker path; in the SWE-bench Pro image `ansible-test` ran `git ls-files`, died,
and the step read F2P=0/4 — the validator's placeholder for "suite never ran".
`git clone --local` (hardlinked objects) works. Treat `SWEBENCH-ERROR:` as unmeasured.

**The API can serve stale rows.** SQLite WAL on the macOS Docker bind mount: the
API's pooled connections alternated between the old and new `per_step` within one
second while fresh connections and the worker saw the new one. `docker compose
restart api` cleared it. Suspect this before suspecting the code when the UI
disagrees with `sqlite3 data/app/harness_lab.db`.

## 2026-11-12 — A step-1 solve still pays for a second step, and a lab_root agent can read the lab's history

First live per-step run (tetris-v2-K-046, claude + Haiku 4.5, `default` prompt,
`lab_root`): every per-step number reconciled with the stream-json `result` events and
Haiku list prices (step 1 $0.1067106 + step 2 $0.1077296 = `cost_usd` $0.21444; billed
tokens 151,318 + 507,521 = 658,839). Solved 10/10 at step 1; step 2 changed nothing
(identical snapshots) yet cost 50% of the spend, 77% of billed tokens and 55% of wall
time. That second step is structural: the loop stops on "step changed nothing" or on
the `ALL MILESTONES COMPLETE` sentinel, and the step-1 prompt does not teach the
sentinel, so a run solved in step 1 can only stop after step 2. Changing that is the
open loop-stop decision (ADR needed; it changes `agent_steps`/cost comparability).

In that idle step the agent ran `git log --all | grep tetris` and `git show` on the
commit that added the tetris-v2 tests and rubric, then read `task.yaml` and ran its
tamper guard. A `lab_root` run's cwd is `/app` — the whole lab checkout — and no mask
level covers `.git`, `workspace/` or `data/runs/`, so earlier runs' solutions
(`workspace/tetris-v2-K-043/main.py`) are readable from every arm. K-046 did not open
them, but nothing prevents it: a contamination risk for comparisons.

## 2026-11-12 — "Claude" authored commits because `.git/config` said so, not because an agent committed

PR #45 showed its two commits authored by `Claude <claude@anthropic.com>`. No agent ran
`git commit`: this repo's `.git/config` held a `[user]` block (name Claude), written by
an earlier agent session, and a repo-local identity overrides the developer's global and
`includeIf` identities — so the developer's own commits were recorded as Claude. PRs
#42-#44 separately carried Claude Code's default `Co-authored-by: Claude` trailer. Fix:
the `[user]` block removed, attribution disabled in user settings, an Authorship
section in `.github/standards.md` (mirrored in every adapter), and the
`commit-authorship` commit-msg hook that refuses an agent identity or AI co-author line.
**Check `git var GIT_AUTHOR_IDENT` before trusting who a commit will be recorded as.**

## 2026-11-12 — End-of-run scores hid that every tetris-v2 run was solved in step 1

Regrading three `tetris-v2` claude runs after every step (K-019 lab root, K-020 bare,
K-034 harness prompt) gave 10/10 after step 1 in all three; 74%, 71% and 32% of the
spend came after that. The extra steps re-ran `ls`/`cat`, wrote their own tests,
added unrequested features ("hold piece") and, in K-019/K-020, read
`benchmarks/tetris-v2/task.yaml` and `scoring.md` — the agent went looking for the
grader. No end-of-run score could see it: every run passed, `agent_steps` measures
the stop rule. This is what ADR-0076's per-step reward exists for.

Two traps found on the way. **Replaying edits from stream-json needs GNU sed and a
`python` on PATH** — on macOS, BSD `sed -i` and a missing `python` make the agent's
own `sed -i`/`python - <<EOF` mutations fail silently and the replay diverges; only
byte-comparing the replay with the archived workspace caught it. **Agents read the
harness with `cat`/`sed -n`, not only the Read tool** — K-034 read four governance
docs that way and `parse_claude_events` recorded `files_read: []`. The per-step
credit scans shell reads against the run's surface; the run-level
`governance_read_coverage@v2` still does not (changing it would change a score's
meaning — needs a v3).

## 2026-11-09 — Join opencode usage by session id; never by title or directory

`pull_usage_opencode_db` matched sessions with `title LIKE '%run_id%' AND directory = /app`.
Both conditions were wrong. opencode's `title` agent (the run's own model) writes the
title, so whether a run got tokens depended on what the model wrote. A masked bare arm
runs opencode in `/app/workspace/<run>`, so every bare run recorded nothing. In one
10-run experiment, 7 runs had no tokens or time, and the 3 that did undercounted
(5.8 min recorded for a 109-min run). The reliable key is in the run's own log: opencode
`--print-logs` prints `message=created id=ses_… parentID=…` for every session, sub-agents
included. Two smaller traps in the same reader: it stored the cache as `tokens_cache_read`
while `collect_cost` reads `cache_read_tokens`, and it left opencode's reasoning tokens out
of output. Rules:
(1) Join on an id the lab controls or observes, never on text a model writes.
(2) Before trusting a usage reader, check that every arm of a real experiment has the
field. A metric one arm lacks is a gap, not a difference.
(3) The opencode store dies with the runner container. A missed read cannot be
backfilled, so test the reader against a real DB (the host's
`~/.local/share/opencode/opencode.db` has the same schema).

## Rebuilding an exception with `type(exc)(message)` breaks any subclass with its own signature (2026-09-23)

The auditor's retry ended with `raise type(last)(f"{last} (after N attempts)")`.
`GatewayHTTPError.__init__` takes `(code, body)`, so three 5xx in a row produced
`TypeError: GatewayHTTPError.__init__() missing 1 required positional argument: 'body'`
— and the comparison page stored *that* as its failure, hiding the gateway's real answer.
The tests only covered the 4xx (no retry) and the 5xx-then-success paths, never
"5xx exhausts the retries". Fixed with a helper that rebuilds a `GatewayHTTPError` from
its own fields and falls back to the base `TransportError` for any other odd signature.
Rule: when re-raising "the same kind of error with a longer message", never assume a
one-string constructor; either mutate `.args`, wrap, or rebuild from the fields.

## A model with no declared limit never compacts, and the server pays for it every turn (2026-09-22)

opencode's overflow check is `count >= limit.context - min(limit.output, 32000)`, and a
custom-provider model with no `limit` gets `limit.context = 0`, which short-circuits the
check to false. No `lmstudio-*` card in `opencode.json` declared one, so a session against
the 41k LM Studio box simply grew past the window; LM Studio truncated the prompt from the
middle on every turn, the single cached prefix never matched again, and each call paid the
full prefill. Measured with `max_tokens=1` on a 35k-token prompt: 60s cold, 0.4s identical,
1.1s with one turn appended, 60s again after any request with a different prefix (a title
call, a second session, the LM Studio chat window). Prefill is ~580 tok/s on that box, so
the harness's ~14k-token startup prompt alone is ~25s cold — that is the slow "hola", and
no context setting changes it. The window is now read off the host at launch
(ADR-0063); the lever for the cold first call is prompt size and LM Studio's engine
settings, not the window. Also: `OPENCODE_SMALL_MODEL` was `openai/qwen/qwen3.5-9b`, a
model the OpenAI provider does not have — a small-model tier that points at the executor's
own host evicts its prefix on every title.

## The gate that stops a repository runs before anything can argue with it (2026-09-09)

Shell repositories were ending the audit wizard with "the runner ships only Python and
Node toolchains" while the runner had been executing bash all along: `python:3.11-slim`
ships **bash 5.2.37** with its Debian base, so adding Shell support needed no line in
`Dockerfile.runner`. Verified with `docker run --rm python:3.11-slim bash --version`.
Check the base image before assuming a toolchain is missing — the block was a
classification table, not a capability. (`/bin/sh` there is dash, which is a separate
fact with teeth: a command using `[[`, `<(` or `pipefail` must be handed to `bash -c`
or it fails for a reason that has nothing to do with the code under test.)

The structural half is the ordering. In `tasks.py::_audit_repository_body`,
`collect_evidence()` — and therefore `next_step` — runs **before** the LLM has proposed
a single case, so per-case classification cannot feed the gate in one pass. Wanting it
to is what makes this look like a rewrite. It is two stages instead: a deterministic
pre-LLM gate that asks the capability table, and a post-LLM plan permitted exactly one
transition, `unsupported_runtime → continue`. One direction only, or a model's proposal
would be reopening a door a fact closed (ADR-0035).

Three smaller ones found on the way:

- **The validation lane was receiving the entire lab `.env`** — `GATEWAY_TOKEN`, GitHub
  tokens, Langfuse keys — through `build_run_environment()`, while running an audited
  repository's own `make test`. Shared helpers spread by being convenient; the lane
  that runs untrusted code and the lane that calls a model had drifted into one
  environment builder.
- **`network_mode="none"` needs a phase, not a flag.** Cutting the validation lane's
  egress outright breaks `git clone`, `npm ci` and `uv sync`. The existing
  `provision`/`setup`/`validate` tag is what makes "no network, but only while running
  their code" expressible at all.
- **A test whose verdict depends on the developer's machine is not a test.** Two new
  shell tests failed locally for host reasons — `get_executor()` resolving to Docker,
  which cannot see a macOS `/var/folders` temp path, and `_portable_timeout` correctly
  dropping its `timeout` prefix on a box with no coreutils `timeout`. Both were the
  test reaching outside the repository, not the code being wrong: pin the executor and
  shorten the deadline through `command_deadline_seconds` instead.

**Why it matters:** the bug was one conflated question — *what is this repository
written in* answering *what does this case need to run* — and it cost a whole class of
repositories. Neither half was wrong on its own.

**How to apply:** when a single boolean gates a whole flow, check whether it is
answering one question or two. Then look at *when* it is computed: a gate that runs
before the information that would correct it needs a second stage, and that stage must
be allowed to move in one direction only. See
[ADR-0056](../docs/adr/0056-cases-are-matched-against-runner-capabilities.md).

## A file can be both the thing you hide and the thing you read (2026-09-09)

Every `injected` arm was dying in provisioning with a message that pointed at the
wrong repository:

    SkillsSyncError: Adapter file is missing the generated-skills sentinels.

Two correct decisions collided. ADR-0049 masks the lab's `CLAUDE.md` inside the
runner, because the agent's cwd sits under the lab root and Claude Code collects
`CLAUDE.md` from every ancestor. And `inject_harness` *reads* `CLAUDE.md` — it is
the document the generated-skills region is spliced into. So the runner copied
its own empty mask stub over the client repository's governance and then refused
to splice it.

The tell was already in the codebase. `RUNNER_GOVERNED_SOURCE_DIRS` carves
`.github/` out of the same mask, with a comment saying that masking it would make
an injected arm project nothing. The carve-out had been reasoned out for the
*directories* the projection reads and never extended to the *files* it reads.

Three things worth carrying:

- **Ask of every mask: does anything on this machine still need to read what I am
  hiding?** A mask is not a property of a file, it is a property of a file *for a
  reader*. Here the agent must not see it and the projection must. The answer was
  to give the projection a different root (the worker stages the real bytes into
  the run directory, which no mask covers), not to weaken the mask.
- **An error naming a file is not an error about that file.** `splice` complained
  about the target's adapter file, which was the only innocent party; the reader
  goes to the client repository and finds nothing wrong. A check that runs *before*
  the copy, naming the source path and the tool, turns a six-frame traceback into
  one sentence.
- **Reproduce a container failure without the container.** Writing an empty
  `CLAUDE.md` into a temp "lab root" and calling `project_harness` reproduced it in
  seconds. The mask is just an empty file; nothing about the diagnosis needed
  Docker.

See [ADR-0055](../docs/adr/0055-the-projection-reads-its-adapter-files-from-outside-the-mask.md).
Related: [[a-single-bind-mount-decides-what-use-my-local-repo-can-mean]].

## A Docker bind mount is a watched tree, not a window (2026-09-07)

Rooting the folder picker at `${HOME}` did not just widen what the API could read
— it made Docker Desktop crash, twice, with the engine taking the whole lab with
it:

    service fs failed: injecting event blocked for 60s

Docker Desktop injects filesystem events into the VM for **every path it shares**,
so a shared home directory means it is watching `~/Library`, every cache and every
synced folder. That volume of events blocks the `fs` service past its timeout and
the engine dies. macOS confirms the diagnosis independently: sharing `$HOME` makes
it pop the TCC prompt "Docker would like to access data from other apps".

`:ro` does not help. Read-only limits writes, not the events the host generates.

The rule: **a bind mount root is a working set, not a search space.** Size it by
what the container needs to touch, not by what would be convenient to browse. The
default is now the directory the lab itself sits in
(`${HARNESS_LAB_LOCAL_SOURCES_DIR:-..}`), and the variable moves that root rather
than narrowing it — one line of configuration is a much smaller cost than an
engine that stops.

The near-miss underneath it: this was chased for a while as "Docker being flaky",
and I told Marcos there was no evidence in the logs pointing at the mount. There
was — in the crash dialog, which I had not asked to see. A component named in an
error message (`service fs`) is a hypothesis, and two crashes on the day one
mount changed is not coincidence.

Related: [[a-real-home-directory-is-not-a-fixture-path-is-dir-raises]],
[[a-single-bind-mount-decides-what-use-my-local-repo-can-mean]].

## A real home directory is not a fixture: `Path.is_dir` raises (2026-09-07)

Turning the folder picker on by default meant rooting it at `${HOME}` instead of
an explicitly configured directory. The listing worked in every test and then
returned a 500 on the first real call:

    OSError: [Errno 95] Operation not supported: '/host-sources/.obsidian-cli.sock'

`Path.is_dir()` does not return False for things it cannot stat — it raises. A
home directory is full of them: unix sockets seen through a shared mount, dead
symlinks, and directories the container may list but not stat. One of them
anywhere beside the repositories took down the whole picker, and no fixture built
out of `tmp_path` and `mkdir` will ever contain one.

Two consequences worth carrying:

- Any code that walks a directory a *person* owns needs its stat calls guarded
  and the entry skipped, not just its path checks. Same for `iterdir()` itself on
  a directory that may be unreadable — answer with the service's own error so the
  caller gets a message rather than a 500.
- Reproduce that class of failure by monkeypatching `Path.is_dir` to raise for
  one name. Building a real socket in `tmp_path` fails on macOS anyway:
  `OSError: AF_UNIX path too long` — the 104-byte sun_path limit against pytest's
  nested tmp directories.

The wider lesson: a default that widens what the code sees (an opt-in root
becoming `$HOME`) is not a config change. It is a new input domain, and the tests
that passed were describing a directory nobody actually has.

Related: [[a-single-bind-mount-decides-what-use-my-local-repo-can-mean]]

## A single bind mount decides what "use my local repo" can mean (2026-09-07)

The audit onboarding only knew one way to get source: the operator clones into
`data/checkouts/<target_id>` and the lab watches that path. Two failures came
out of it on a real machine — a repository already cloned under a different name
was invisible, and the page waited forever without saying what it was watching.

The design was not decided by preference. The API container mounts `.:/app` and
nothing else, and the runner mounts the lab root the same way, so a repository
anywhere else on the host is invisible to the API *and* to every run and gate
check. That makes "point the lab at my folder" impossible and "copy my folder
into `data/checkouts/`" the only shape that works. `git clone --no-hardlinks` is
the copy: without `--no-hardlinks` git links the new object store into the
operator's own repository, which is exactly the coupling ADR-0042 exists to
avoid, and the flag costs nothing because the copy is local.

Three things the tests only found once the endpoints were exercised:

- **The endpoint shape is a silent failure mode.** `GET /api/checkouts` returned
  `{"checkouts": [...]}` while the page rendered the response as an array. No
  error, no list — an operator told to clone a repository they already had.
  Service-level tests cannot see this; the API tests added alongside them can.
- **A copy inherits the mount path as its origin.** The clone's `origin` came
  out as `/host-sources/...`, a path that names nothing outside the container
  and stops existing when the mount changes. It is now reset to the repository
  URL the target declares — the same origin the guided clone leaves.
- **Ordering, not validation, is what protects an attached checkout.**
  `pin_checkout` writes back the path it inspected, so `inspect_checkout` had to
  become path-aware *first*; otherwise pinning silently un-attaches the clone
  the operator chose.

**Why it matters:** the fence of ADR-0042 — a local source is accepted only
under `data/checkouts/` — is what makes all of this safe, so it stayed untouched
while everything around it changed. The picker is off unless
`HARNESS_LAB_LOCAL_SOURCES_DIR` is set, mounted read-only, and resolves every
path *before* checking containment, because a symlink would otherwise pass a
textual check and read anything the container can see.

**How to apply:** when a feature asks the lab to reach outside itself, look at
the bind mounts before the API design — they usually decide the answer. And when
a request is really a UI shape (a list, a wrapper), assert the shape at the
endpoint: the service tests will pass either way. See
[ADR-0045](../docs/adr/0045-a-checkout-the-lab-can-be-given-not-only-watched.md).

## A form gate that contradicts its own next branch is a bug (2026-09-06)

The *Full harness* wizard refused any selection whose cases defined fewer than
two prompt variants, and the branch immediately after it documented — in a
comment — that equal prompts are perfectly sound, since the bare arm still hides
the whole governed catalog. Two branches of one `if`, disagreeing. The gate had
been written for `tetris-v1`, the only case in the lab with a prompt *family*;
every case written in the Cases screen defines exactly one variant, `default`,
so the lab's founding comparison was unavailable on the cases the lab is used
on.

**Why it matters:** the same-prompt design is not a degraded version of the
prompt-family one — it is the *cleaner* one. A hand-written `sdlc` prompt tells
the agent to read governance, so `naive` vs `sdlc` moves the instruction and the
harness together. A plain `default` prompt says nothing about governance, so
running it on both arms isolates the harness. The gate blocked the better
experiment and permitted the more confounded one.

**How to apply:** when a validation rule and the comment next to it disagree,
believe the comment and delete the rule — and when a user says a refusal makes
no sense, check whether the refusal is defensible before offering workarounds
around it. Two related habits this turned up:

- **Say what a design measures instead of refusing it.** The same-prompt pair
  now runs and carries two advisories: the treatment is the skill catalog, not
  the harness as a unit; and inside the lab both arms keep the governance body
  of `CLAUDE.md`, because the sync engine rewrites only its generated skills
  region. A truly bare control needs `harness_source` against an external repo.
- **Every cross-product needs per-case resolution.** `ExperimentService.launch`
  applied each arm's `prompt_variant` without checking the case declares it; the
  prompt lookup falls back to `""`, so the run went out with no task, inside one
  arm, reading as that arm losing. Batch mode had resolved this per case since
  ADR-0023 and experiments had not — the second copy of a rule is where it goes
  missing. See
  [ADR-0044](../docs/adr/0044-a-single-prompt-case-can-carry-the-full-harness-pair.md).

## A transport migration is found by grepping the wire, not the helper names — 2026-09-04

ADR-0043 collapsed "four" gateway call sites onto `llm_transport`. There were
five: `case_draft_service.py` had its own `_call_gateway_chat`, so on a
subscription-only lab every judged feature worked while **Draft it with AI** in
the wizards still answered "Gateway not configured". The plan's inventory had
been built from the helper names people remembered (`call_gateway`,
`_worker_gateway`, `_gateway`), not from the request shape. The one string every
copy shared was `chat/completions`.

The same pass showed how documentation drifts in this repo: not by lying about
a feature, but by describing a *previous* layout. The in-app Guide still walked
"Cases → Runs → Score → Compare" after the nav had been regrouped around three
questions; four guides said `claude` needs host mode a month after it moved into
the runner image; two docs in the same directory disagreed on whether the API is
on the LAN (it is not; the Vite dev UI on `0.0.0.0:5173` proxies it, so the
answer is "yes until `make set-password`"). And `docs_catalog.py` is a
whitelist: a new guide is invisible in the web Guide page until it is registered
there, which is why the three newest workflows (target repositories, skill
validation, run continuation) had docs nobody could reach from the UI.

**Why it matters:** the UI, the docs served in-app and the markdown on disk are
three copies of "how to use the lab". A change that lands in one place reads as
a contradiction from the other two, and a reader cannot tell which copy is
current.

**How to apply:** when auditing docs against code, build the fact sheet from
the code first (nav groups from `Shell.tsx`, routes from `api.py`, transports
from `llm_transport.py`, harness rules from `run_service.preflight`) and check
every document against that one sheet, so the fixes agree with each other.
When migrating a duplicated call, grep for the wire format (`chat/completions`,
`choices[0]`) rather than for function names. When adding a guide under
`docs/`, register it in `docs_catalog._DOCS` or it does not exist in the app.

## Under `native`, a lab-side skill is invisible to the agent — 2026-09-04

`apply_skill_views()` writes into the LAB root; `inject_harness()` runs only for
`harness_source == "injected"`. Under `native` the agent's cwd is the cloned
target, so neither governed nor `--extra-skill` skills reach it. A
`native` + `skills_extra` arm therefore equalled its own control while hashing
as a distinct condition — the ADR-0041 failure one level down. Fixed by
`add_skills_to_native()` in `bench_run.py` + `linker.add_skills()`, which copies
only the named skills into the workspace's native skills dir and, unlike
`link_tool`, **prunes nothing** (the target's own `.claude/skills/` is that
team's work).

Related: `validate_design` used to warn "more than one harness factor varies"
on the most common correct design — staging a skill *and* asking for it. They
now collapse to one axis when every pair of arms is comparable on both sets at
once; crossing sets (one arm adds X unasked, another asks for a Y it already
had) still warn.

## A container cannot clone a private repo, and should not try — 2026-09-04

Target repositories were cloned inside the run container, which runs with
`GIT_TERMINAL_PROMPT=0`, no ssh agent and no tokens by design. Correct for a
public repo, fatal for every private one — i.e. every client repository. The
fix was not to teach the lab about credentials but to invert the direction:
the engineer clones in their own terminal into `data/checkouts/<target_id>`,
the lab detects and pins it, and `clone_source(target)` returns that path
everywhere the repository URL used to be read (runner provisioning, readiness
mirror, case derivation, ref resolution). Git takes a path anywhere it takes a
URL, so no downstream code needed a second branch (ADR-0042).

Three gotchas found doing it:
- **`validate_repository_url` rejects local paths on purpose** (a URL starting
  with `-` is remote command execution to git). Local sources needed their own
  `validate_clone_source`, which accepts a path only under `data/checkouts/`.
- **`target_id` becomes a directory name**, so it is a path boundary —
  `../escape` would put a clone anywhere on the host. `validate_target_id`
  refuses rather than sanitising, because a silently rewritten id would not
  match the directory the operator was told to clone into.
- **The API container and the operator's shell see different paths.** The
  guided command must use `host_repo_root`, the same value the runner uses for
  its bind mounts; printing the container's own `/app/...` view is useless in a
  terminal.

## A red run against an unfamiliar repo is ambiguous — 2026-09-04

"The agent failed" and "this repository does not build in the runner image"
look identical in a failed run and mean opposite things. The gate check
(`gate_check_service` + the `gate_check_target` Celery task) provisions the
pinned commit, runs setup and the repo's own validation commands with no agent
and no model, and records **which phase** failed (provision / setup /
validate). It runs through the same executor as objective validation, so it
needed no new infrastructure. Refuses to run at all when the repository has no
runnable validation command — a check that passes by running nothing reads as
success and is worse than no check.

## "With skill" means available, not invoked — 2026-09-04

First real Lane B comparison (`does-adding-caveman-improve-outcomes`, 5+5 runs,
claude/haiku, one AI-drafted snake case): verdict "No difference observed",
success 100% both arms, every secondary delta within run-to-run variance. The
person read it as "slightly worse". Parsing the step events told the real
story: in **0 of 5** treatment runs did the agent call the `Skill` tool or read
`caveman/SKILL.md`. The final messages were the usual "Perfect! ✅ ALL
MILESTONES COMPLETE" prose. The treatment never happened.

Mechanism: `--extra-skill` projects the staged skill into the agent's native
skill view (the run's init event lists it among 40 skills). That makes it
*available*. `caveman`'s description triggers on "/caveman", "caveman mode",
"be brief"; the case instruction says none of that, so the model had no reason
to open it. The lab's number was correct and its caveats honest, but the
number measured "skill on the menu", not "skill in use".

Two other things learned reading the same data:
- **Baseline is the governed harness, not a bare model.** `build_effective_skills`
  = every governed skill under `.github/skills*` minus `--disable-skill` plus
  `--extra-skill`. "Without the staged skills" therefore means CLAUDE.md +
  ~25 governed skills. Arm descriptions now say so.
- **`total_tokens` ≈ output tokens for Claude Code runs.** `usage.json`
  reports `input_tokens` in the hundreds because cache reads are not counted;
  a context-reduction skill's main effect (less input re-read per turn) is
  invisible in that metric. Reading `cache_read_input_tokens` from the stream
  events would be needed to see it.

**Why it matters:** a comparison can be statistically clean and still answer
the wrong question when the treatment is conditional on the agent's choice.
Skills are exactly that kind of treatment.

**How to apply:** decide which question the experiment asks. "Does it help
when asked for" → tick *Enforce skill use* (`skills_invoke`, the arm's prompt
asks for it at every step; the case stays identical across arms). "Does it
help unasked" → leave it off and read which skills the agent opened. Either
way read `skill_invoked` per arm before the delta; an arm at zero now carries
the caveat at the top of the verdict. Implemented in ADR-0041.

## A validation "command" the assistant writes can pass the gate and still measure nothing — 2026-09-03

The AI case assistant returned heredocs (`node - <<'NODE' … NODE`,
`python - <<'PY' … PY`) as validation commands. The case stores one shell
command per line, the wizard's textarea joins the array with newlines and
splits it again on save, so the script body became twenty separate entries.
Most were refused by `_looks_executable` (`NODE`, `}`, `const …`). The
dangerous part was what survived: `node - <<'NODE'` on its own passes the
heuristic (first token `node`), and `sh -c` runs it with an empty stdin — exit
0, a pass with no assertion behind it. The gate from ADR-0040 forces a command
to exist; it cannot see that a fragment is meaningless.

Fix (in `case_draft_service.py`): the system prompt states the one-line rule,
every returned command is screened with the runner's own heuristic
(`is_executable_command`) plus a multi-line refusal, the model gets one
corrective turn if nothing runnable is left, and the response carries
`dropped_commands` with reasons so the wizard shows the gap under the draft.

**Why it matters:** an LLM will happily satisfy a schema ("array of bash
commands") with something the executor cannot run as a unit. Screening at the
same line the runner uses is the only place the refusal is both early and
consistent; prompt wording alone is a suggestion.

**How to apply:** whenever a model-generated artifact feeds an executor, run
the executor's own acceptance check on it before handing it to the person, and
return the rejects with reasons rather than silently narrowing the output.
Also: run pytest here as the Makefile does, `PYTHONPATH=$PWD/src pytest …` —
the venv's editable install points at an older checkout path, so a bare
`pytest` collects against stale code and reports modules as missing.

## Two base CSS rules made the UI look inconsistent between pages — 2026-09-03

After the Xmartlabs branding pass the wizard pages looked broken in two ways
that the short pages did not show: the sidebar groups drifted apart, and every
checkbox or radio sat in the middle of its row with the label pushed to the far
right. Neither was page-specific. Both were base rules in `frontend/src/styles.css`
interacting with layout that only appears on long pages:

- `.nav` is a grid with `flex: 1` so the sidebar footer sticks to the bottom. A
  grid's default `align-content: stretch` then spreads the rows over the whole
  sidebar height, which is the page height — invisible on a short page,
  obvious on a long one. `align-content: start` fixes it.
- The base `input { width: 100%; padding: 10px 12px }` is meant for text fields
  but also hits `type="checkbox"` and `type="radio"`; the control fills the flex
  row and its `<span>` label is shoved to the edge. `.skill-checkbox` had a local
  override for exactly this; the fix is one global rule for both input types.

The third symptom — pick-list rows of different widths, and a selected row whose
description was unreadable — came from reusing `.primary` / `.secondary` (CTA
buttons, `inline-flex`, width-to-content) as row selection states. Rows now have
their own rule under `.pick-list > li > button`, and "selected" tints instead of
filling.

**Why it matters:** a page-level symptom that reproduces on several pages is a
base-rule defect; fixing it in the page (inline styles, per-page overrides) hides
the next occurrence. The inline styles in `SubjectValidation.tsx` also referenced
tokens that do not exist (`--surface-sunken`, `--card-bg`, `--radius`), so they
silently fell back to hard-coded colours that ignored the theme.

**How to apply:** when a control looks wrong on one page, screenshot two other
pages that use the same class before touching the page. Chrome headless
(`--headless=new --screenshot=… <url>`) against the running dev server is enough
to verify a CSS change without a browser session. Keep JSX free of `style={{}}`
in this codebase: every value it needs already exists as a token or a class.

## The mode is what licenses the conclusion, so a missing mode gets borrowed — 2026-09-03

The most common thing an outside team brings to this lab is not a harness change.
It is a thing they built — an agent behind a local API, an MCP server, a service
wrapper — plus a skill that drives it, and the question "does it work reliably?".
The lab ran that fine: cases, arms, repetitions, `task_validation_pass`, the
sandbox. Every number was already computable. What was missing was a **name** for
the question, and the three declared modes of
[ADR-0028](../docs/adr/0028-evaluation-modes.md) all asked about the harness.

A missing mode does not stay missing. It gets borrowed from the nearest one, and
`harness_evaluation` was the nearest one. That produces a correctly computed pass
rate attached to a claim the design cannot support — "the tool passed 8 of 9" read
as evidence about the harness, when no arm was ablated and there is no baseline to
subtract. It is the exact defect ADR-0028 was written to prevent, reintroduced
through the label instead of through the display.

The second finding sits underneath it: the hard gate of the new mode is not
statistical, it is **a case with at least one executable validation command**. It
has to be a block, not a warning, because the failure is silent and downstream.
`build_validation_spec` skips non-executable entries without complaining, so a case
whose validation is prose yields zero commands → `parse_objective` returns
`passed=None` → `task_validation_pass` is never emitted → `scored_runs == 0` → the
pass rate is `None`. The experiment runs in full, costs real money, and answers
nothing without saying so.

**Why it matters:** the modes are the product. Adding a fourth is not feature
creep — it is the only way to stop a fourth question from being answered with a
third question's authority. And the honesty of the number depends on a gate placed
*before* money is spent, not on a caveat rendered after.

**How to apply:** when a new use of the lab appears, ask what sentence it licenses
before asking what it computes. If no declared mode's sentence fits, the mode is
missing — do not run it under the closest label. When adding a gate, trace the
failure path to where it surfaces: if the bad input degrades into an empty result
rather than an error, it must be refused at creation. Known limit accepted here:
the gate can force a validation command to exist and to be executable; it cannot
force it to be able to fail (a smoke check that the game starts cannot fail once
the game starts). That judgement stays with whoever writes the case. See
[ADR-0040](../docs/adr/0040-subject-validation-mode.md).

## Resetting the database under a live stack splits it in two — 2026-09-02

`make app-reset-db` runs `scripts/reset_db.py` from the **host** while the `api`
and `worker` containers hold `data/app/harness_lab.db` open across a bind mount.
SQLite coordinates concurrent connections through a WAL plus a `-shm`
shared-memory index, and that index is not coherent across the container/host
boundary on macOS (virtiofs): the host process believes it is the only
connection and unlinks the `-wal`/`-shm` the containers are still using.

The two containers then read **different files**, and nothing errors. The API
keeps serving and writing to the file it already had open; the worker reads the
new one. An experiment created afterwards shows its runs as `pending`, Celery
delivers `execute_run`, the worker looks the run up, gets `None`, returns, and
logs `succeeded in 0.004s`. The runs never execute. The API's rows exist only in
an unlinked inode and are lost the moment the container restarts — so the
symptom is discovered hours later and the evidence is destroyed by the fix.

Diagnosis shortcut: `docker exec <container> sh -c 'ls -l /proc/*/fd' | grep
'\.db'`. A `(deleted)` marker on `-wal`/`-shm` means the split has already
happened. Comparing a row count from inside the worker against the same query
from the host confirms it in one step.

`app-reset-db` now refuses while `api` or `worker` are running. The same hazard
applies to any host process that opens the database while the stack is up,
including an ad-hoc `sqlite3` session — closing it unlinks the sidecar files.
Read the database through `docker compose exec api`, or stop the stack.

Not fixed at the root: the database still lives on the bind mount. The
structural fix is a named volume (WAL and `-shm` on the VM's own filesystem) or
having the worker reach the API over HTTP instead of sharing a file.

## A comparison fails silently in five places at once — 2026-09-02

The first real two-arm corpus (12 Tetris runs) produced a verdict that looked
complete and was unreadable. Five independent mechanisms, none of which raised
anything:

1. **`clone_run` dropped `experiment_id`/`condition_label`.** They live on `Run`,
   not `RunConfig`, so the careful field-by-field config copy did not carry them.
   Four "Run again" repetitions executed the design and counted towards nothing.
   This is the *third* time this exact shape has bitten (see the continuation
   entry below): fields on the row rather than the config are the ones that get
   dropped.
2. **Only SWE-bench auto-scored.** Everything else parked in
   `awaiting_manual_score` forever, so the comparison fell back to
   `task_validation_pass` — for tetris-v1 a smoke check ("does it start"), which
   is level_2 of a 0-10 rubric and cannot fall once both arms start. It reported
   100% → 100% as *no difference observed*.
3. **The stop criterion was arm-dependent.** `_STOP_RE` matches a prose sentinel
   the `sdlc_full` prompt teaches and `naive` does not, and the no-progress
   fallback existed only in the SWE lane. `agent_steps 10 → 1` measured prompt
   authorship. Verified against the artifacts: K-009 had ten `# claude run
   started` headers, K-011 had one and ended at `turns=50` — roughly 70 vs 50
   turns of real work, reported as a 10× efficiency win.
4. **Zero observed spread cleared the noise floor.** `stdev=0.0` on both arms
   made `abs(delta) > 0.0` true, so three "beyond noise: yes" verdicts rested on
   having measured the same thing twice.
5. **`runs.yaml` out-voted `--harness`.** A web run whose id collided with a
   pre-declared cell ran one CLI and recorded another (K-001..003: config
   `claude`, `metadata.json` `opencode`), while the *model* flags used the
   opposite precedence. Inconsistent precedence between two flags on the same
   command is a bug generator.

**Why it matters:** not one of these produced an error, an empty field, or a
suspicious-looking number. Each one produced a plausible result for a comparison
that was not being made. An evaluation lab's failure mode is not the crash — it
is the finding.

**How to apply:** before reading any experiment verdict, check (a) can the
success metric physically move for this case, (b) did both arms terminate under
the same rule, (c) is there any observed spread at all. Those three questions
would have caught four of the five. And when a stored file and a CLI flag can
both name the same thing, decide the precedence once and announce a divergence
rather than resolving it silently. See
[ADR-0038](../docs/adr/0038-every-run-leaves-with-a-score.md).

## An evaluation lab's real product is the refusals — 2026-08-30

Building v2 (experiments, scores, findings) the pattern that kept recurring is that
the valuable code is what the system *declines* to say:

- an experiment that varies harness **and** repository is rejected at creation, not
  rendered as an average later (`validate_design`);
- a mean from one observation reports **no** spread — `stdev=0.0` would claim perfect
  consistency from a single run;
- a delta with no spread to compare against reports `exceeds_spread=None`, not `True`;
- a missing cost emits **nothing**, never `0` — zero is a claim, and a flattering one;
- a confidence interval under N=3 returns `None`;
- a maturity level above 2 without evidence is refused outright;
- the DeepEval adapter emits nothing when unwired, because "not measured" and "scored
  badly" must never merge in an aggregate.

Each of these is one branch. Together they are the difference between a lab and a job
runner that renders averages — and none of them would ever be missed by a test that
only checked the happy path, because the failure is a plausible-looking number.

Second recurring rule, now applied five times: **a new field enters `condition_hash`
only when it differs from the historical default** (`skills_disabled` non-empty,
`target` present, `harness_source != lab_root`). Break it once and the whole
historical corpus re-bins against hashes it never had.

Third: an axis added to `RunConfig` must be carried by every path that claims to
reproduce a run — clone, retry, continuation, and each repetition of a batch. That is
four call sites per axis, and missing one splits an arm invisibly.

## cwd *is* the harness condition, and the fingerprint has to follow it — 2026-08-30

Claude Code and opencode auto-load their adapter file and native skills from the
process cwd. `bench_run` already knew this — it set `agent_cwd = workspace` for one
hardcoded case so a "bare" SWE arm would not silently inherit the lab harness. So the
`native` / `injected` comparison (ADR-0031) is not new machinery, it is that side
effect promoted to a declared condition.

The trap is in the fingerprint, and it fails **silently**. `build_harness_fingerprint`
reads `skills-lock.json`, which a cloned repository never has. Point both arms at it
and they produce the same `harness_hash` → the same `condition_hash` → one arm where
there should be two, and every downstream comparison quietly stops comparing. No test
fails, no error appears; the numbers just become meaningless. Hence
`build_workspace_harness_fingerprint`, which hashes what is actually readable from the
agent's cwd (native skill dirs, projected agents, adapter files, governance docs).

Three smaller things the implementation forced:

- **Project before the baseline commit.** Provisioning commits the workspace so
  `git diff HEAD` is only the agent's work. A harness copied in afterwards lands in
  that diff as though the agent wrote it.
- **Copy, never symlink.** `link_tool`'s symlink strategy encodes a path relative to
  the root it writes into; a lab skill is unreachable that way from a clone, and the
  archived workspace has to stand alone anyway.
- **Gate the fingerprint move on the declared condition, not on `agent_cwd`.** The SWE
  lane has set `agent_cwd = workspace` since long before this existed; keying off it
  would re-bin every historical SWE arm against a hash it never had.

**And do not compare `lab_root` against `native`** — they differ in the harness *and*
in whether the agent works inside the repo at all. Two changes, one number. See
[ADR-0031](../docs/adr/0031-harness-source-is-a-run-condition.md).

## A pinned commit must survive every path that "repeats" a run — 2026-08-30

Target repositories (ADR-0029) resolve a ref to a SHA at run creation. The obvious
implementation resolves it once, in `create_run`, and is wrong in four places:
`clone_run`, `continue_run`, `_resume_config` (retry) and `create_batch` all produce
runs that claim to be the *same arm* as an earlier one. Any of them re-reading
`main` silently splits one arm in two the moment someone pushes — and the split is
invisible, because both runs are labelled `main`.

So resolution takes a `pinned_commit` short-circuit, and every one of those paths
passes the stored commit instead of the ref. `create_batch` resolves once per
`(repo, ref)` and hands the same SHA to all N repetitions, for the same reason
`_preflight_key` deduplicates preflight: the expensive call is not the problem, the
*inconsistent* call is.

Two smaller traps in the same change:

- **`condition_hash` must hash the commit, never the ref.** Hashing `main` makes two
  different experiments share an arm. And it enters the identity *only when
  non-empty*, following the existing ablation rule — otherwise every historical arm
  re-bins.
- **`git ls-remote <url>` with a url starting with `-`** is argument injection
  (`--upload-pack=…` is remote command execution). `validate_repository_url` is a
  security boundary, not input tidying; and `GIT_TERMINAL_PROMPT=0` +
  `ssh -oBatchMode=yes` turn a missing credential into an error instead of a hung
  API worker.

**Also discovered, and it changes what is feasible next:** `skills_sync` is already
fully root-parameterized (`discover_skills(root)`, `link_tool(root, …)`,
`render_tool(root, …)`, `project_agents(root, …)`). Only
[bench_run.py](../scripts/bench_run.py) hardcodes `REPO_ROOT`. Injecting the harness
into a *foreign* workspace is therefore a `source_root`/`target_root` split, not a
rewrite — which is what makes the `native` vs `injected` comparison in
[the v2 roadmap](../docs/roadmap-lab-v2.md#phase-2--harness-conditions-on-an-external-target)
tractable.

## Langfuse v4 takes traces only over OTLP, and its SDK cannot backdate them — 2026-08-27

Wiring run traces to Langfuse looked like a two-line SDK call. It was not:

- **`POST /api/public/ingestion` returns 400** for every event type except
  `score-create` and `sdk-log` on v4, and `/api/public/traces|spans|generations`
  return 404. The only route that accepts spans is **OTLP over HTTP** at
  `POST {host}/api/public/otel/v1/traces` — protobuf *or* JSON, no gRPC — with
  `Authorization: Basic b64(pk:sk)` and the required header
  `x-langfuse-ingestion-version: 4`.
- **`langfuse` SDK 4.15.0 `start_observation()` has no `start_time`/`end_time`.**
  It stamps spans from the live OTEL clock, so it physically cannot express when
  a step that ran last month actually ran. Any post-hoc exporter must write
  `startTimeUnixNano`/`endTimeUnixNano` itself.

So `observability/` hand-builds OTLP/JSON and posts it with `urllib`. Attribute
names matter and are not guessable: `langfuse.trace.name`, `langfuse.session.id`,
`langfuse.trace.tags`, `langfuse.observation.type|input|output|level`,
`langfuse.observation.metadata.<key>` (that prefix is what makes a key
filterable in the UI), `langfuse.observation.usage_details` / `.cost_details` as
JSON *strings*. In OTLP/JSON, int64 is encoded as a string (`{"intValue": "7"}`)
and `bool` must be checked before `int` — `bool` is a subclass of `int`, so the
naive order encodes `True` as `1`.

**Why it matters:** the no-dependency choice is not purity. `Dockerfile.runner`
stays unchanged, which is the precondition for a future live emitter importing
the same module from inside a run container.

**How to apply:** when an observability vendor's SDK looks like the obvious
answer, check first whether it can express *historical* time. If it cannot, the
wire format is the API. See
[ADR-0027](../docs/adr/0027-langfuse-traces-rebuilt-from-run-artifacts.md) and
[guide-langfuse-observability.md](../docs/guide-langfuse-observability.md).

## SWE-bench score IS the objective validation — and the loop must stop on it — 2026-07-09

First real agent SWE-bench run (pallets__flask-4992-K-001, qwen3.6-35b) resolved
the issue in STEP 1 (`resolved=True F2P=1/1 P2P=18/18`), then burned 9 more
steps creating a main.py Flask demo and "fixing" werkzeug deprecations, because
the continuation prompts (`generate_continuation_prompt`) and `_STOP_RE` were
Tetris-hardcoded. Fixes: (1) SWE lane now has its own continuation prompt
(issue + live F2P/P2P status, "library source only, no main.py"), (2) the step
loop runs `swebench_common.evaluate` after EVERY step and breaks on resolved —
the stop criterion is objective, never the agent's own claims. (3) The worker
auto-scores swe_bench_instance runs from the validation verdict (resolved→10,
else→0, binary like official SWE-bench) and completes them — no manual score.
Re-validate also triggers auto-score (heals runs parked awaiting_manual_score).

**UI gotcha**: objective validation runs AFTER the state flips to
awaiting_manual_score and takes ~1 min (repo reinstall), so a single fetch on
state-change shows "No validation yet" forever — the RunDetail now retries the
validation/attribution fetch for ~2 min after a terminal state.
**Runner image gotcha**: jinja2 is a dev-group dep, but bench_run's skill
ablation imports the skills_sync renderer inside the container → added to
Dockerfile.runner explicitly (like pygame). Rebuild the runner image
(`docker compose build` / the runner build target) to pick it up.

## Per-run skill ablation = regenerate the generated views, not move files — 2026-07-08

Skill ablation (ADR-0011) works because the agent-visible skill surfaces
(`.opencode/skills/` symlinks AND the `BEGIN/END GENERATED SKILLS` block in
OPENCODE.md) are all *generated* by skills_sync from `.github/skills*`. So
"disable a skill for one run" = `link_tool(root, tool, filtered_skills)` +
`render_tool(..., skills=filtered)` per tool, and "restore" = the same call
with the full list — idempotent, which is what makes restore-at-start a valid
crash-recovery strategy (plus `atexit` for normal exits; worker concurrency 1
rules out interleaving). Removing symlinks by hand would have left OPENCODE.md
still advertising the skill (not a real ablation) and required bespoke cleanup.

Two hash traps: (1) `condition_hash` must include `skills_disabled`/staged
hashes ONLY when non-empty, or every historical arm re-bins; (2) the harness
fingerprint hashes skills via `skills-lock.json` (source, unchanged by
ablation), so without the explicit field an ablated arm would hash identical
to baseline except via the OPENCODE.md governance hash — too subtle to rely
on. Also exclude OPENCODE.md from `harness_git_dirty` during ablation.

**How to apply:** any per-run harness variation (agents, hooks) should follow
the same pattern: mutate only generated artifacts through the engine, record
the variation explicitly in metadata + condition hash, restore by full re-sync.

## LLM structured output: separate JSON-only call + tolerant parser — 2026-07-08

The audit's per-item verdicts (útil/redundante/mejorar per skill/doc/hook) come
from a SECOND gateway call with a "respond with ONLY a JSON array" prompt, not
from parsing a fenced block out of the prose audit. `parse_findings` never
raises: strips fences, extracts the outermost `[...]` from prose, drops invalid
items, coerces unknown enums, and returns `{"items": [], "parse_error": ...}`
on total failure — the audit.md is already written by then, so findings trouble
can never fail the audit. The UI treats findings as optional garnish.

## 2026-08-18 — A gateway card can be routable and dead, and `os.environ/` hides it

Every `gateway/lmstudio-qwen3.6-35b*` card failed while the same model answered fine
on the Direct lane. Nothing was mis-named: `/model/info` showed
`lmstudio-qwen3.6-35b` -> `lm_studio/qwen/qwen3.6-35b-a3b` @ `http://192.168.0.223:1234/v1`,
exactly LM Studio's id, and 19 completed runs on that alias prove the wiring. The rig
was simply off — ping 100% loss, ARP incomplete — and the Direct lane looked alive
only because its 15s TTL cache still held the last good listing.

Two structural findings behind it:

- **`os.environ/VAR` does not disable a card when the variable is unset.** LiteLLM
  resolves it to `None` and then sends the request to its *own* default host. The
  deployed gateway serves `lmstudio-qwen3.5-9b-mac16` with `api_base: null` — a card
  that can never work but advertises itself as available.
- **Fallback targets are not validated at startup.** `gemini-2.0-flash` sat in the
  chain after Google retired it (404 verified), so the escalation a powered-off GPU
  depends on was dead while the config read as configured.

**Why it matters:** the lab's `routable` flag answered "can this harness address the
id?", never "is the machine behind it serving the model?", so a dead card looked
launchable and the failure got blamed on the model or the alias.
**How to apply:** `build_model_catalog` now takes the `DirectHostProbe` list the
Direct lane already collected (no extra round-trip) and marks self-hosted gateway
cards `reachable: false` with a reason — host down, id absent from that host, or no
`api_base` declared. It fails open: an unprobed host keeps its verdict `None`. When a
gateway card fails, check the backend host before touching the alias. In ai-gateway,
`test_every_fallback_alias_is_a_declared_card` is now the startup check LiteLLM does
not do.

## Continuing a run: a child run was the cheap design, and two bugs sat next to it

**What happened:** adding "continue a finished run with a new prompt" looked like
it needed runner support — step offsets, merged usage, re-opened artifacts. It
needed none. Modelling the continuation as a *new run* whose workspace is copied
from the parent means it owns its own `data/runs/<id>/`, so there is nothing to
offset or merge. Two flags that already existed did the rest: `--resume` makes
`validate_workspace` keep the seeded tree, and `provision_workspace`
short-circuits on an existing `.git`, so a rehydrated SWE workspace is not
re-cloned. The only genuinely new code was one `shutil.copytree` and a prompt
header telling the agent the inherited workspace is its own.

The one place the lane mattered: a curated case (`RUNS_FILES`) resolves its
prompt from `benchmarks/<case>/`, ignoring `case_spec.json` entirely — so a
continuation had to override `_prompt_source.md` *after* the lane branch, not
inside the DB branch.

Two adjacent defects surfaced while wiring it, both in the same shape — a
`RunConfigCreate` rebuilt field by field, silently dropping the fields added
later:

- `tasks._config_payload` omitted `skills_disabled`/`skills_extra`, and
  `execute_run` overwrites `run.command` with what it rebuilds. **The skills
  ablation never reached the container.** The stored preview command had the
  `--disable-skill` flags; the executed one did not. Every ablation arm run
  before 2026-08-24 measured the full harness.
- `_resume_config` (retry) and `clone_run` dropped the same two fields, so a
  retry or clone quietly changed arms.

**Why it matters:** ADR-0011's whole point is that an ablation is a distinct
condition. A silent drop does not fail — it produces a plausible number for a
condition that was never run, which is worse than an error.

**How to apply:** when a feature can be expressed as a new row rather than a
mutation of an existing one, price that first — immutable artifacts make most of
the hard parts disappear. And treat every hand-rolled `RunConfigCreate(...)` as a
place a future field will be dropped: `_config_payload`, `_resume_config`,
`clone_run` and `continue_run` must all be updated together when the schema
grows. See [ADR-0026](../docs/adr/0026-run-continuation-as-child-runs.md) and
[guide-run-continuation.md](../docs/guide-run-continuation.md).

## claude stream-json per-turn usage undercounts output (2026-08-27)

In `steps/step-N.events.jsonl`, each assistant message's `usage.output_tokens`
is a streaming snapshot taken before the message finished: summing them across
a run gives ~5% of the real total (`1348` vs `26987` on `tetris-v1-K-004`).
`input_tokens` and the cache counters per turn are usable; output totals must
come from the terminal `result` event or `usage.json`. The events also carry
no timestamps of their own — `bench_run.stamp_event_line` now injects
`_received_at` at capture time, and the Langfuse exporter labels anything
older `timing_source: interpolated`. Also: `run_id` is not globally unique
across copies of a run directory, which is why OTEL ids are salted with
`condition_hash`. See
[ADR-0028](../docs/adr/0028-step-traces-carry-per-call-detail.md).

## A subscription token is a whole LLM transport (2026-09-04)

The lab made four independent `POST /chat/completions` calls — the auditor, the
SWE judge, the DeepEval judge, the case summariser — each with its own copy of
the same twelve lines. Collapsing them into `llm_transport` was worth doing on
its own, but the reason it mattered is that **one seam is one place to add a
second transport**: `claude -p --output-format json --tools "" --strict-mcp-config`
is a text-completion endpoint, so a teammate whose only credential is a Claude
subscription now gets audits, judges and summaries with no paid API key and no
gateway. `--tools ""` is the load-bearing flag: it makes the CLI a completion
rather than an agent, so no permission prompt can ever block a worker.

Three things that were not obvious until the code ran:

- **The CLI reports failure in-band.** A bad model name comes back as exit 1
  *plus* `"is_error": true` *plus* the error text sitting in `result`, exactly
  where an answer would be. A parser that reads `result` without checking the
  other two returns an error message as if it were an audit.
- **The audit prompt must go on stdin.** The real one measured 86 KB (clipped
  diff plus harness log). Not over the argv limit, but close enough that a
  larger diff would be.
- **Model names are transport-specific**, so defaults have to be too.
  `gpt-5.4-mini` means nothing to the CLI and `claude-sonnet-5` means nothing to
  a gateway that does not proxy it; a name that is not a Claude id is dropped so
  the CLI picks its own default instead of sending a guaranteed 404.

**Why it matters:** the ordering is the entire compatibility promise. The gateway
is checked first, always, so adding a Claude credential to a multi-model lab can
never move a judge — a test asserts it. Two labs judging the same run over
different transports produce numbers that are only loosely comparable, which is
why each judged score records its transport in provenance.

**How to apply:** when a capability is duplicated more than twice, the third
copy is the signal to extract a seam — the payoff is usually the *next* feature,
not the deduplication. And validate a fallback path end to end before believing
it: this one uncovered `ToolCorrectnessMetric()` being constructed outside its
`try`, where DeepEval eagerly builds an OpenAI client and raises on a missing
`OPENAI_API_KEY`, killing all three trajectory metrics on any lab without that
key. Unit tests never saw it; a real subscription-only run did. See
[ADR-0043](../docs/adr/0043-one-llm-transport-gateway-first-claude-cli-second.md)
and [the plan](../docs/plans/2026-09-claude-native-llm-transport.md).

## Three experiments that could not answer their own question (2026-09-08)

**What happened:** three `harness_evaluation` experiments asked "does the full
harness improve outcomes over the bare model on Tetris?". All three rendered a
confident verdict; none of them could have measured it. The corpus is still in
`data/app/harness_lab.db` and is the cheapest regression fixture this lab has —
every guard below is verifiable against it with no new runs.

**What the evidence showed, in the order it was found:**

- All six full-harness runs carried `skill_invoked = 0.0` — *the treatment never
  happened*. The score existed and the caveat sentence was already written. It
  landed as a footnote under a headline that read as a result.
- The summary never even reached that sentence. It rebuilt each arm's skill list
  from that arm's own `skills_extra`/`skills_invoke`, and a full-harness
  treatment stages nothing: the whole catalog is named on the **control's**
  `skills_disabled`. `score_service.build_context` already unioned the siblings;
  `summarize` did not, so the treatment's list came back empty and the
  observation had nowhere to land. Two readers of the same fact, two rules.
- Four of four opencode runs ended `stop_reason: harness_error` (SSE timeout)
  and were recorded `state=completed, exit_code=0`, then auto-scored 0. The
  worker reads the exit code, not `usage.json`; `bench_run` returned 0 on the
  managed path regardless. A dead connection was averaged in as a model failing.
- Two experiments had a control passing every repetition. Improvement above 100%
  is unrepresentable — the design could not detect the thing it was built for.
- The control arm was never bare. Venue `lab` means the governance body of
  CLAUDE.md, the SessionStart hook, six subagents and `.github/` are present in
  **both** arms; skill ablation rewrites only the generated catalog region. The
  wizard's advisory about this was gated on "both arms run the same prompt",
  which is unrelated to the property it describes.

**Why it matters:** none of these was a missing measurement. Every fact was
already computed, stored and even phrased correctly. The lab simply had no way
to say *this comparison could not have measured the thing it names*, so
everything it knew was expressed as a caveat under a confident sentence — the
one place a reader will not weigh it.

**How to apply:** when a check is worth making, decide where the reader meets it
before deciding how to compute it; a correct sentence in the wrong slot is
indistinguishable from silence. When two components read the same fact (here:
"which skills is this arm about?"), extract the rule or they will drift — the
scorer and the summariser disagreed for three experiments. And a guard computed
on read needs no migration: the bad rows are re-read correctly where they stand,
which is also the argument for never rewriting them. See
[ADR-0046](../docs/adr/0046-an-experiment-reports-validity-before-it-reports-a-verdict.md).

## A timeout is a fact about the lab, not about the agent (2026-09-08)

Four Tetris runs — all four working games — scored 1/10. Nothing in the chain was
about the code:

- **`timeout` alone cannot kill an SDL program.** Plain `timeout <N>` sends SIGTERM;
  SDL converts SIGTERM into an `SDL_QUIT` *event*, which an interactive loop is free
  to ignore. The command wedged, exited 124, and `passed = exit_code == 0` read that
  as "the code fails". Always `timeout -k <grace> <N>`.
- **A `-k` escalation is not enough on its own.** The wedge can sit anywhere, and
  `RunHandle` (executors.py) has no deadline: `wait()` takes no timeout and
  `iter_lines()` is the blocking call. The only shape that works is a
  `threading.Timer` watchdog calling `handle.stop()`, sized from the command's own
  `timeout` budget.
- **"0/1 checks passed" is the worst possible sentence to hand a judge.** It reads
  identically whether an assertion returned the wrong number or the process was
  killed before it could answer — and the judge, which *does* read the diff
  (`judge.py:build_rubric_message`), had no reason to distrust it. Passing the full
  per-check list with an explicit INCONCLUSIVE marker costs nothing and removes the
  ambiguity.

**Why it matters:** two-valued validation forces every unknown into "failed", and a
false zero is worse than a missing one — it averages into `success_rate` and looks
like a measured result. This is ADR-0046's *infrastructure entering the corpus as
capability*, one layer below the crashed harness it was written about.

**How to apply:** when a boolean summarizes a process that can be interrupted, check
whether it can also express "did not run". Then propagate that third value all the
way out — evaluator (exclude, do not zero), judge prompt (refutes nothing), UI (warn,
not error) — because any layer that flattens it back to false undoes the other three.
See [ADR-0048](../docs/adr/0048-a-check-that-did-not-measure-is-not-a-failure.md).

## A different cwd is not isolation (2026-09-08)

The lab assumed "the agent's cwd is the workspace, so it cannot see the lab's
harness". Two archived `native` runs measured what that assumption actually buys:

- **`.claude/` is genuinely not read from an ancestor.** Their `system/init`
  events list only Claude Code's built-in agents, no lab slash commands, no lab
  skills, and no `SessionStart` hook fired. That half of the assumption holds.
- **`.mcp.json` is.** Both runs came up with the lab's `context7` and `git` MCP
  servers while having no `.mcp.json` of their own. And `CLAUDE.md` memory lookup
  walks ancestors by design — `workspace/<run>/` sits under the repo root, which
  is bind-mounted whole at `/app`, so the lab's rulebook was one directory up the
  entire time.
- **`.claude/settings*.json` is read from the project dir, not from an ancestor
  either** — so a workspace-cwd run never sees the lab's `env` block
  (`ANTHROPIC_BASE_URL`, the LiteLLM gateway). Do **not** read
  `apiKeySource: "none"` as proof of a bypass: OAuth-token auth reports that on
  every arm, lab-root included (ADR-0049 erratum). A cwd change can move
  routing, not only governance — so routing is injected as container env.

**Why it matters:** the whole point of a control arm is that it *lacks* something,
and nothing in the pipeline can detect a control arm that quietly kept it. The
only fix that is both tool-agnostic and unbypassable is masking at the container
level — empty tmpfs over directories, read-only empty-file binds over files. No
CLI flag turns off ancestor `CLAUDE.md` lookup.

**How to apply:** when isolating an agent, enumerate what its tools auto-load and
from *where* — cwd, ancestors, `$HOME`, env — then verify against a run's own
`system/init` event rather than against the docs. Two Docker details that cost
time: a bind source must exist on the host first or Docker silently creates a
*directory* there and the mount fails, and an empty `.json` mask is invalid JSON
rather than "no config" (use `{}`). See
[ADR-0049](../docs/adr/0049-a-bare-arm-is-a-masked-runner-not-a-lab-root-run.md).

## The first valid bare-vs-harness run, and what the instrument got wrong (2026-09-08)

The control arm was finally bare (masks verified in `system/init`: built-in agents
only, no MCP servers, no hooks), and the experiment was still invalid — every reason
was a defect of the *instrument*:

- **The judge scored equivalent code 10 and 2 on identical evidence.** Not a
  sampling problem: `tetris-v1`'s rubric levels 3-10 describe gameplay ("opens a
  window", "rotation works") that neither a diff nor a headless smoke can establish,
  and the prompt asked the judge both to award what the diff shows and to withhold
  what only a check proves. Fix the *rubric* before sampling: levels now carry
  `[check]`/`[diff]`/`[manual]` tags, `[manual]` is never awarded, `[diff]` needs a
  quoted citation. `tetris-v1` honestly caps at level 2; `tetris-v2` ships ten
  tests so the grade is objective (ADR-0050).
- **The judge's number never reached the experiment page.** `Validation.subjective`
  is not a score row, so the 10/2 split was invisible in the deltas. `rubric_score`
  is now a JUDGEMENT-provenance registry score — visible, never the success metric.
- **"Total tokens" showed the harness using *fewer* tokens while costing +71%.**
  `collect_claude_usage` summed `input_tokens` (42-146 per run — the uncached
  sliver) and ignored `cache_read_input_tokens` (724k vs 184k). A harness is paid
  for in cache reads. `billed_tokens` is the new headline; `total_tokens` keeps its
  meaning (ADR-0034: never redefine a name). Highlights now carry a direction.
- **The case matrix kept the last repetition only** (`verdicts[label][case_id] =
  passed`), so 1-of-5 and 5-of-5 rendered identically. Now all-pass per cell.
- **`apiKeySource: "none"` is not a gateway bypass** — the lab-root arm shows it
  too; it is how OAuth reports. Recorded as an erratum on ADR-0049.

**Why it matters:** none of these would have shown up on a harness that helped; they
showed up because the bare arm made the comparison sharp enough to notice.

**How to apply:** before trusting a delta, ask three questions of the instrument —
is the outcome metric graded finer than the thing being varied, does the opinion
metric reach the page where the delta is read, and does the cost metric count what
the treatment actually adds. `make ladder` (ADR-0051) now creates the four
single-factor rungs consistently; `make harness-adopt REF=` (ADR-0052) makes every
run attributable to a release.

## Never write the live SQLite from the host (2026-09-09)

`data/app/harness_lab.db` went "database disk image is malformed" after a day of
host-side writes (`sqlite3` one-liners, `import_case.py`, `ladder.py`, a seed
call) while the api and worker containers were writing the same file through a
Docker bind mount on macOS. WAL over a bind mount with two hosts is a known
corruption path. The symptoms before the diagnosis were subtle: imported cases
"disappeared", Celery tasks "succeeded" in 0.14 s having done nothing (the
exception was swallowed into a RunEvent), and re-validations never landed.

**Recovery that worked:** `sqlite3 corrupt.db ".recover" | sqlite3 new.db`, then
`PRAGMA integrity_check`, then `VACUUM INTO` the real path with api+worker
stopped. Every row survived except one `case` row, which the startup seed
re-imported from `task.yaml`.

**How to apply:** when the app is up, go through the API (`POST /api/cases/seed`,
`/validate`, `/experiments`) — never `sqlite3` or a script against the file.
Also: `_autoscore_run` only scored `awaiting_manual_score` runs, so a re-validate
never re-judged; it now re-scores a run whose previous score came from the judge
or an objective verdict (never a human's). See ADR-0053.

## A badly formed case reports a number about itself (2026-09-09)

Two cases written from the talos audit ran four experiment arms and produced nothing
about talos, and nothing about the lab's own template either. Every defect was silent:

- **A shell `for` loop exits with its last command's status.**
  `for t in {workspace}/tests/test_*.sh; do bash "$t"; done` reported
  `task_validation_pass = 1` while the same run's stored detail read
  `rc=2 CONFIG ERROR`. Fifty-four failures ahead of one pass is a green check. The
  fix is `|| exit 1` **inside** the loop body.
- **A repo-relative path with no `{workspace}` resolves against the lab's own root.**
  `bash benchmark/model/run-model.sh` exited 127 in both arms. The file existed; the
  path did not. Exit 127 in a validation check is almost always this.
- **`source: "git_target"` is not a case type.** `bench_run.py` decided "is this
  SWE-bench?" by calling `spec_provisions_repo()`, which answers *"does a repository
  have to be cloned?"* — true for **every** case paired with an external target. So
  from step 2 both talos cases were told to resolve a GitHub issue, shown an empty
  list of target failing tests, and told to run `python -m pytest` in a repository
  with no Python. Both arms. The injected arm reacted worse (6.6× turns, 9× cost,
  zero diffs), which would have been reported as a treatment effect.
- **A run parked in `awaiting_manual_score` with an empty rubric is a case defect, not
  a scoring backlog.** `_llm_judge_rubric` returns at `if not rubric` — silently — and
  the run looks like something a human just has not got to.
- **Runner capabilities include POSIX tools, not only interpreters.** Every talos test
  died because the image shipped no `jq`, and the agent's own `apt-get install jq`
  lived in the *agent's* container, which the later validation container does not
  inherit.
- **A repository's harness may not be in a dotted directory.** talos keeps sixteen
  skills in `skills/` and four hooks in `hooks/`; four drifted path taxonomies all
  named only `.claude/`-style roots, so the audit reported "no harness at all" — worse
  than "unknown", because it reads as a finding.

**Why it matters:** none of these show up as an error. A false green occupies the same
field, in the same colour, as a real one, and it averages into every aggregate above
it. The lab was shaping every result it produced, on a repository it had audited once —
and the whole point is to audit repositories it has never seen.

**How to apply:** when a check is written, ask what would make it fail, and then make
the lab ask the same thing — statically for the shapes that are always wrong, and
empirically by running the check against the clean tree before any agent touches it. A
check that passes on the untouched repository has told you nothing. And when one
predicate is used to answer two questions, name the second one: the SWE-bench leak was
a single `elif` reusing a function whose docstring already said it answered something
else. See [ADR-0057](../docs/adr/0057-a-case-is-validated-before-it-can-measure-anything.md)
and [the case-formation guide](../docs/guide-case-formation.md).

## A source edit is not a deploy: api and worker do not reload (2026-09-10)

The wizard kept proposing `bash tests/test_*.sh` and refusing to save it after the
fix that repairs exactly that hint had landed. Nothing was wrong with the fix. The
`api` (uvicorn, no `--reload`) and `worker` (celery) containers bind-mount `.:/app`,
so the files on disk were current — and the *processes* were seven minutes older
than the edit. `docker compose exec worker python -c "import …"` is no help here:
it starts a fresh interpreter that reads the disk, not the memory of PID 1.
Compare `/proc/1` start time with the source mtime instead.

**Why it matters:** two rounds of the same defect look like the fix does not work,
and "do we have to iterate like this on every repository?" is the right question
to ask when a lab keeps contradicting itself. The answer was operational, not a
design gap — but it read as one.

**How to apply:** after editing anything under `src/`, `docker compose restart api
worker` (the runner image needs `docker compose build runner` only when
`Dockerfile.runner` changes). A stored audit keeps the case plan the old worker
computed; re-run the audit once after restarting. Do not read a stale process as a
failed fix.

## A case that measures nothing costs the same as one that does (2026-09-10)

`audit-talos-4` spent $4 and 43 minutes on two arms that both scored 0, and the
gates all worked: the case they gated was "run talos's own suite", a suite that
was already red at the pin (39 failures in `lib/evidence.sh` / `test_seal.sh`),
under a `|| exit 1` loop the repository itself never uses. Nothing measured the
harness's thesis — model judges, code certifies — because the audit asked *what
could an agent do here* over 700 characters of each skill and the paths (not
the text) of the hooks. Three lessons, now enforced (ADR-0058):

- **Ask the auditor for claims, not tasks.** The unit is a harness component
  with `claim / trigger / observable / lab_counterpart / needs`; cases name the
  components they exercise. `use_cases` produced smoke tests for every repository.
- **Run the suite at the pin before the model speaks.** `health_at_pin` is one
  hardened validation-lane run; it would have flagged the red suite before any
  case existed, and it is the only deterministic signal the auditor gets.
- **A harness can be installable.** talos copies itself into a target project
  (`scripts/talos-install.sh`); a bare checkout has its skills under `skills/`,
  invisible to Claude Code, and no settings. A "native" arm on such a checkout
  is a bare run, and an "injected" arm that overwrites its `CLAUDE.md` while
  leaving `AGENTS.md` is a contradictory one. `shape: installable` + the
  `target_repo` need make the audit say "not measurable here" instead.

Costs to remember: two planner-tier passes per audit, and one container run
for health. Still far cheaper than one experiment that cannot answer.

## Three instrument defects inverted an experiment's reading (2026-09-10)

`audit-talos` (1 case × 1 rep, native vs injected) reported 0 % success on both
arms and rubric scores 0 vs 4. Both agents had in fact done the task; re-read
with the instrument fixed, both are 2/2 checks and 8/10.

- **`pytest {workspace}/tests/x.sh`** — the drafter wrote it, nothing refused it,
  it exits 4 on every run. `case_validation` now refuses interpreter/file
  mismatches (`pytest`/`python` on `.sh`, `bash` on `.py`, `node` on `.sh`) with
  the right interpreter as the suggestion.
- **`diff.patch` omitted new files.** `git diff <provisioned>` lists tracked
  files only; a run whose whole deliverable is a new file shipped an empty patch,
  and the judge scored the arm that *also* edited tracked files 0 for "not adding
  the test" a PASS check had just executed. `write_diff_patch` now runs
  `git add --intent-to-add --all` on the archive first; `.gitignore` still holds.
  Old runs can be rewritten from their archive with the same function.
- **The judge trusted the diff over the checks**, and graded the repository's
  pre-existing behaviour that a check's *output* described (jq fail-open) as the
  agent's shortfall. The prompt now states that a PASS check and the file list
  decide existence, and that check output describes the repository; the judge
  also receives `files_modified` from `usage.json`, workspace-relative.

**How to apply:** when two arms tie at 0 with a cheap judge, read the checks
and the archive before the verdict — an impossible check or an empty diff is the
instrument, not the agent. After a fix, re-validate (`POST /api/runs/{id}/validate`)
re-judges any run the judge scored before; a human score is never overwritten.
The continuation loop (generic step-N prompts) still drives scope creep and cost —
native spent a third step editing hooks the task said not to touch. Not fixed.

<!-- Add new learnings above this line -->

## "Audit a third-party repo" fails at provisioning when setup_commands mismatch the language — 2026-08-31

Every audit run against the `deepseek` target (`deepseek-ai/deepseek-harness`) died
with a generic "agent error"/`failed` (exit code 1) in `[1/5] Preparing`, before the
agent ran a single step. Root cause was not the harness, the model, or the network:
the target's `setup_commands` were `["pnpm install --frozen-lockfile", "pip install -e ."]`
and `validation_commands` `["pnpm test", "pytest -x -q"]` — a **JS/pnpm repo with
Python setup lines bolted on**. `_provision_git_target` in `scripts/bench_run.py`
runs each `setup_commands` entry as operator-authored shell (`subprocess.run(...,
shell=True)`) and `sys.exit`s on the first non-zero rc. `pip install -e .` on a repo
with no `setup.py`/`pyproject.toml` returns rc=1 ("does not appear to be a Python
project"), so the run stops with "The agent would have started against a half-built
repository". The UI surfaces only the terminal `failed` chip, so it reads as an agent
fault.

Two structural notes: (1) `setup_commands`/`validation_commands` are free-text shell
with **no language validation** at target creation — the DB will happily store a
command whose toolchain the repo cannot satisfy. The `Dockerfile.runner` image
**does** ship Node 20 (npm/pnpm/yarn) *and* Python 3.11 (pip/uv/pytest) plus git —
so `pnpm install` actually **succeeded** here; the abort was `pip install -e .` on a
repo with no `pyproject.toml`/`setup.py`. The runner does **not** ship Go or Rust.
So the real invariant is not "must be Python" but "each configured command's
toolchain must exist in the repo AND in the runner image". (2) the failure is
pre-agent, so `STEPS PARSED 0` and "No metadata.json" are expected, not extra bugs.

**Fix applied:** added a clean Python target `click` (`pallets/click`, `main` →
`36baa15`) via `POST /api/targets` — zero runtime deps, `requires-python >=3.10`,
test deps just `pytest`, so `setup_commands=["pip install -e .", "pip install pytest"]`
and `validation_commands=["pytest -q"]` install in seconds on the runner's Py3.11.
Archived the `deepseek` target so it cannot be reused.

**How to apply:** the runner image supports **Python and Node**, not Go/Rust/Java.
A target is safe when it is a pip-installable Python project (`pyproject.toml`/
`setup.py`) OR a Node project (`package.json`), and every configured setup/validation
command names a toolchain the repo actually has (don't put `pip install -e .` on a
repo with no `pyproject.toml`, or `pnpm install` on a repo with no `package.json`).
Before creation, inspect a repo with `POST /api/targets/inspect` (or per-target
`GET /api/targets/{id}/readiness`): the "Runtime compatibility" check warns — fail-open,
not blocking — when the ecosystem is unsupported or a command's toolchain is missing.
`readiness_service.runtime_compatibility(paths, setup, validation)` is the rule.

## In-platform sandboxes: worker-launched containers with dynamic host ports — 2026-07-08

Per-run validation sandboxes (ADR-0010) let the user run/inspect a run's code in
the browser: a `terminal` (ttyd) and a `desktop` (Xvfb+x11vnc+noVNC, to SEE a
pygame/Tetris window). Key mechanics:
- **One image, two modes** (`Dockerfile.sandbox`, ~870 MB, build-only) selected by
  `MODE` env in `scripts/sandbox_entrypoint.sh`. ttyd needs an arch-aware static
  binary (`ttyd.aarch64` on Apple Silicon). Xvfb screen size = what pygame's
  `display.init()` reports (proved the display works even without xdpyinfo).
- **Worker owns the socket** (ADR-0002): API can't launch; `POST /sandbox` marks a
  `Sandbox` row `starting` + enqueues `start_sandbox`; frontend polls `GET`; iframes
  `url`. Launch uses `containers.run(ports={'7681/tcp': None})` then
  `reload()` + read `container.ports[...]['HostPort']` — **docker-out-of-docker maps
  the port on the HOST**, so the browser reaches `localhost:<dynamic-port>`.
- **Isolation**: mount a COPY at `data/runs/<id>/sandbox-<kind>/` (host path), never
  the archived workspace, so exploring doesn't mutate the artifact.
- **Cleanup**: explicit Stop + `cleanup_run_container` (extended to also kill sandbox
  containers) + `delete_run` deletes `Sandbox`/`Audit` rows. Startup reaper + idle
  TTL are still-open hardening.

**Gotcha**: in zsh `status` is a read-only special var — don't use it as a loop
variable in test scripts (use `st`). Security: ttyd/noVNC have no auth — fine only
because it's local single-user on localhost.

## SWE-bench generic runtime needs per-repo env pinning (Python + dep constraints) — 2026-07-08

Running a SWE-bench instance with a generic `git clone + uv pip install -e . +
pytest` (instead of the official per-instance Docker images) works for
pure-Python repos, but **only with the right pinned environment**: (1) the
correct Python version (SWE-bench flask 2.3 needs **3.11** — under 3.13 old
werkzeug hits `ast.Str` removal errors), and (2) dependency constraints the
repo's own `pyproject` doesn't enforce (flask 2.3 declares `Werkzeug>=2.2.2`
but its code imports `werkzeug.urls.url_quote`, removed in werkzeug 3.0 → must
pin `Werkzeug<3`). The HF dataset rows carry NO dependency pins, so the lab
keeps a small `_ENV_SPECS` map (`scripts/swebench_common.py`) of
`repo -> {python, pins}`; adding a repo = add its entry. Also install the repo's
compiled `requirements/tests.txt` when present (pins test deps like blinker).

Parsing: `pytest -rA` prints per-test summary lines as `PASSED <nodeid>`
(status FIRST) — the regex must match status-first, not id-first.

**Why it matters:** without the pin/version the repo won't even import, and the
F2P/P2P validation silently reports 0/N. The golden-patch self-test
(`make swebench-selftest INSTANCE=…`) proves the whole harness with **zero model
tokens** (golden → resolved, empty → not) and is the right first de-risk before
wiring an agent. Flagship instance: `pallets__flask-4992` (add `text=` to
`flask.Config.from_file`), F2P=1 P2P=18, installs in seconds.

## LLM audit runs in the worker by shelling out to bench_audit.py — 2026-07-07

The qualitative audit (`scripts/bench_audit.py`, an LLM HTTP call up to minutes)
is exposed in the web via the same enqueue+poll pattern as validation (ADR-0008).
`audit_run` (Celery task) shells out to `scripts/bench_audit.py` with
`env={**os.environ, **build_run_environment()}` so the `.env` gateway URL's
`localhost` is rewritten to `host.docker.internal` (the worker is a container),
then reads `data/runs/<id>/audit.md` back and stores it on the new `Audit` row
(status running→done|failed). `AuditService` owns only DB/status (no gateway/
subprocess) so the API can import it. `bench_audit.py` got the same
`resolve_or_synthesize_run` treatment as bench_run so web `K-NNN` runs (absent
from runs.yaml) can be audited.

**Why it matters:** reuses the battle-tested CLI as the single source of truth,
avoids an `src → scripts` import coupling, and keeps the worker as the home for
slow/side-effecting work (ADR-0002).
**How to apply:** any new "read run artifacts + call an LLM/gateway" feature
belongs in the worker with `build_run_environment()`, surfaced via a DB status
row the frontend polls — not inline in the API (would block the request).
On Linux the worker needs `extra_hosts: host.docker.internal:host-gateway`
(Docker Desktop/macOS resolves it by default).

## Web runs are "managed": --fresh owns the workspace lifecycle end-to-end — 2026-07-07

The old "set RESUME=1" failure came from `validate_workspace()` (`bench_run.py`)
`sys.exit`ing on a stale `workspace/<id>/`, combined with the web/manual flow
`return 0`-ing before Phase 5 `close_run()` (which is interactive-only), so
workspaces were never cleaned. Fix: the runner adapter always passes `--fresh`
for web runs (ADR-0006). `--fresh` means "managed/automated run": on start it
resets a stale workspace instead of exiting; at the manual return (the web happy
path, after execution) it calls the new `finalize_workspace()` to archive
`workspace/<id>/` into `data/runs/<id>/workspace/` and remove it. CLI manual
runs omit `--fresh` so `make bench-close` can still find the live workspace.

**Why it matters:** with auto-incremental unique ids (`<case>-K-NNN`, unique
across DB *and* disk) the collision can't happen anyway, but `--fresh` +
`finalize_workspace` are what stop workspaces accumulating and make a fresh web
run reliable without manual `rm -rf`.
**How to apply:** any new "the API owns scoring/lifecycle" runner path should key
destructive workspace behavior off `--fresh`, never off `score_mode=manual`
alone (the CLI also uses manual). The fail-open branches now `return 1` (not 0)
so a run that built nothing is recorded `failed`, not falsely `awaiting_manual_score`.

## Preflight + visible enqueue failure turn silent no-ops into actionable errors — 2026-07-07

`RunService.create_run` now runs `_preflight` before persisting: empty
`executor_model` (non-dry-run) → `RunPreflightError` (HTTP 422); a `gateway/`
model with `GATEWAY_TOKEN` unset/placeholder → 422 too (token read from `.env`
via `config.read_env_file_value`, not a Settings field). And `_publish` now marks
the run `failed` with a reason when enqueue raises, instead of leaving it
`pending` forever. Consequence for tests: fixtures that create runs must pass a
non-`gateway/` `executor_model` (e.g. `"stub-executor"`) or `dry_run=True`, else
preflight rejects them.

## Deleting a run spans 3 stores with no DB cascades — 2026-07-07

`RunService.delete_run` (ADR-0007) removes, explicitly (no FKs/cascades exist):
`Validation`, `Attribution`, `Artifact`, `RunEvent`, `Run`, then the 1:1
`RunConfig`. Disk: `data/runs/<id>/` + `workspace/<id>/` via
`settings.workspace_dir` (new) — path-guarded to resolve under `repo_root` so a
`run_id` is never a free-text path. Container teardown is worker-only (API has no
docker socket, ADR-0002): `delete_run` enqueues `cleanup_run_container`. A
`running` run is refused (409) unless `force=True`.

## Claude Code gateway model names must match native alias resolution — 2026-07-07

Claude Code reads the generated `.claude/agents/*.md` `model:` frontmatter for
subagents. To make gateway-routed Claude Code behave like direct Claude Code, use
Claude Code's native aliases in agent frontmatter (`opus`, `sonnet`, `haiku`,
`fable`) and pin them with `ANTHROPIC_DEFAULT_OPUS_MODEL`,
`ANTHROPIC_DEFAULT_SONNET_MODEL`, `ANTHROPIC_DEFAULT_HAIKU_MODEL`, and
`ANTHROPIC_DEFAULT_FABLE_MODEL`. The gateway must expose the exact full model IDs
Claude Code resolves to (`claude-opus-4-8`, `claude-sonnet-5`,
`claude-haiku-4-5-20251001`, `claude-fable-5`). Nonstandard local aliases such as
`claude-opus-4.8-oauth` may work as legacy LiteLLM names, but they bypass Claude
Code's native alias/capability model and can produce misleading entitlement or
model-selection behavior.

## Roadmap checkboxes need a three-way done-ness distinction, not binary — 2026-07-07

Auditing `docs/roadmap-local-webapp.md` against real code (not memory or
assumption) surfaced that "done" splits into three states that a plain
checkbox can't express on its own: (1) fully done, (2) backend/model
implemented but no frontend action exists yet (e.g. case edit/delete/clone,
score submission, artifact indexing all have working endpoints/services but
no UI), and (3) declared/scaffolded but never actually exercised by any code
path (e.g. `RunState.draft`/`scoring`/`auditing` are valid enum members that
no transition ever assigns; `POST /api/runs/{run_id}/start` was never built
because the worker's poll loop makes it unnecessary).

**Why it matters:** checking a box because the enum member or database column
exists overstates progress; leaving it unchecked with no note understates it
and hides real backend work already done. Either mistake makes the roadmap
stop being trustworthy as a status source.
**How to apply:** when marking a roadmap/checklist item, grep for the concrete
code artifact that proves it (function name, enum assignment, route
decorator, frontend call site) before flipping `[ ]` to `[x]`. If the backend
exists but nothing calls it from the UI, leave it unchecked with a one-line
note naming what's missing, rather than checking it or leaving it silently
blank.

## Cross-process cancellation needs a DB-persisted signal, not shared memory — 2026-07-07

The API and worker run as separate Docker Compose processes/containers with no
shared memory, so `cancel_run` cannot directly reach into a running
`subprocess.Popen`. Instead, `Run.cancel_requested` is a DB column the API sets
when `state == running` (without touching `state` itself), and the worker's
drain loop in `_run_process_streaming` polls it every 0.5s, calling
`process.terminate()` when true. The worker itself then makes the terminal
`cancelled` transition via `RunService.mark_cancelled`, never the API.

**Why it matters:** if `cancel_run` had flipped `state` to `cancelled`
directly, a still-running worker would silently overwrite it back to
`completed`/`failed` via `mark_finished` once the subprocess exited — a race
between two processes writing the same row. Only one process should ever own
the terminal-state write for a running run.
**How to apply:** for any cross-process action on a live run/job when
API and worker are separate processes, signal through a DB column the owning
process polls, and let the owning process write the terminal state — don't
let the initiator guess the outcome.

## Worker log streaming needs threads per stream, not sequential reads — 2026-07-07

`subprocess.Popen` with separate `stdout`/`stderr` pipes will deadlock if you
read one stream to completion before touching the other (the child blocks once
the unread pipe's OS buffer fills). `worker.py` reads each stream in its own
thread into a shared `Queue`, and the main loop drains the queue into
`RunEvent` rows as lines arrive — this is what makes logs appear while the
process is still running instead of only at exit.

**Why it matters:** a naive `for line in process.stdout: ...` followed by
`for line in process.stderr: ...` works for tests with tiny output but silently
hangs on any real command that fills a pipe buffer.
**How to apply:** always drain multiple subprocess pipes concurrently (threads
+ queue, or `selectors`), never sequentially, when both streams matter.

## Docker-first local app wiring — 2026-07-07

The local web app is now practical to run under Docker Compose with the repo
mounted into the API and frontend containers. The API image must copy
`README.md` and `src/` before `uv sync` or Hatchling/uv editable builds can fail
while resolving the package.

**Why it matters:** the Docker path is the most stable way to keep macOS and
Linux behavior aligned for this project.
**How to apply:** prefer `docker compose build api frontend` and keep the
compose services pointed at the mounted repo root.

## Run config contract must stay aligned — 2026-07-07

`RunConfigCreate` needs to expose every flag consumed by the runner adapter
(`resume`, `dry_run`, prompt/model settings, and max steps) or preview and run
creation tests will fail even when the database model already has the field.

**Why it matters:** the runner adapter is the first real contract between UI and
benchmark execution.
**How to apply:** update the schema and the adapter together whenever command
flags change.

## How this template is wired for agents — 2026-06-22

The single source of truth for governance and skills lives in `.github/`; native
tool layouts (`.claude/`, `.opencode/`, `.agents/`, `.codex/`) are **generated** by
the `skills_sync` engine. Never hand-edit generated skill links or the region
between the `BEGIN/END GENERATED SKILLS` sentinels in adapter files — run
`make sync-skills` instead, and `make check-sync` to verify nothing is stale.

**Why it matters:** hand-edits to generated artifacts are silently overwritten and
break the CI drift gate.
**How to apply:** edit `.github/skills/*.md` (source) and regenerate.

## bench_run.py fails open without opencode — 2026-07-07

When the `opencode` binary is missing, `bench_run.py` does NOT fail: it prints a
manual-paste fallback and exits 0. A worker environment without opencode makes
runs look successful while the agent never executed. The Makefile guards this
with `command -v opencode || exit 1`; any new execution backend must guarantee
opencode is present (it is baked into `Dockerfile.runner`) or check explicitly.

**Why it matters:** this silent fallback was the root cause of "web runs succeed
but do nothing" while the CLI worked.
**How to apply:** treat opencode availability as part of the runner contract;
never run `bench_run.py` in an environment where its presence is unverified.

## Docker-out-of-docker bind mounts use host paths — 2026-07-07

The Celery worker runs in a container but launches runner containers through the
host daemon (`/var/run/docker.sock`). Bind-mount sources in `containers.run` are
resolved by the HOST daemon, so they must be host paths — the worker's `/app` is
meaningless there. Compose passes `HARNESS_LAB_HOST_REPO_ROOT: ${PWD}` for this.

**Why it matters:** using the container-local path silently mounts an empty or
wrong directory and the run fails confusingly.
**How to apply:** any DooD `volumes=` argument must come from
`settings.host_repo_root`, never from `settings.repo_root`.

## Env injection beats the mounted .env (setdefault) — 2026-07-07

`scripts/create_run.py:_load_env` populates `os.environ` with `setdefault`, so
env vars injected into the runner container win over the repo `.env` bind-mounted
inside it. `build_run_environment()` exploits this to rewrite
`localhost`/`127.0.0.1` gateway URLs to `host.docker.internal` without touching
the host `.env`.

**Why it matters:** it is the clean seam for host-vs-container networking; no
file rewriting or duplicated config needed.
**How to apply:** put per-environment overrides in the container env, keep
`.env` as the host-truth file.

## Trial condition_hash: identity vs provenance — 2026-07-07

`metadata.json` now carries a hashable trial condition (`provenance.py`). The
`condition_hash` (the experimental "arm" id) is computed over ONLY the
agent-facing harness (skills/agents/docs/hooks hashes), model settings
(temp/context/models), prompt-template hash, and case hash. Provenance-only
fields — `lab_git_commit`, `harness_git_dirty`, `runner_image` — are recorded
but deliberately EXCLUDED from the hash: they say *how* a trial was produced,
not *which arm* it is. So two reps of the same arm share a `condition_hash`
even across lab-code commits, while an ablation (one skill edited) flips it.

**Why it matters:** this is what makes runs groupable into cells/arms and makes
"a diff of two fingerprints = the description of the ablation" true.
**How to apply:** when adding a field, decide if it changes the *arm* (→ into
`compute_condition_hash`) or just documents provenance (→ manifest only).
Prompt hash uses the template (`prompt_source`), NOT the generated `prompt.md`,
which embeds per-run ids and would break arm stability.

## bench_run writes metadata before executing — 2026-07-07

`write_metadata` runs in phase "[1/5] Preparing", before the agent executes. So
`data/runs/<id>/metadata.json` (incl. the provenance manifest) exists even if
the run is later killed mid-execution — useful for verifying provenance without
paying for a full multi-step LLM run. But note: `--dry-run` returns BEFORE
metadata is written, so dry-run does not produce metadata.json.

## Validation runs in the worker, not the API — 2026-07-07

Objective validation executes a container (via the same `RunExecutor` as runs),
so it must run where the docker socket is mounted: the **worker**. The API
process has no socket (ADR-0002), so `POST /api/runs/{id}/validate` enqueues a
`validate_run` Celery task and the frontend polls `GET /validation`. Running it
inline in the API fails with `DockerException: ... /var/run/docker.sock ... No
such file or directory`.

**Why it matters:** any feature that spawns a container belongs in the worker.
**How to apply:** API endpoints that need docker must enqueue, never call the
executor inline.

## Runner container: sh -c, not sh -lc; pygame via uv pip — 2026-07-07

Validation commands run with `sh -c`. A login shell (`sh -lc`) re-sources
profile and drops the runner image's `/opt/venv/bin` from PATH, so `python`
resolves to a system interpreter without the installed deps (pygame not found).
Also: the uv-created `/opt/venv` has no `pip`; install extra runtime deps with
`uv pip install --python /opt/venv/bin/python <pkg>`, not `/opt/venv/bin/pip`.

**Why it matters:** both bugs made the Tetris smoke fail spuriously.
**How to apply:** run in-container commands with `sh -c`; manage the venv with uv.

## Composite score gates subjective by objective — 2026-07-07

The multidimensional score keeps objective/cost/effort/subjective separate and
only combines them in `composite`, which caps the manual functional score at 1.0
when objective validation FAILED (a high manual score is not credible when the
code does not run). Dimensions are never collapsed before that.

**Why it matters:** preserves the signal an ablation needs; prevents a
misleading single number.
**How to apply:** new metrics go in as their own dimension; only `compute_composite`
merges them, explicitly.

## opencode log signals for harness attribution — 2026-07-07

The opencode step logs (`data/runs/<id>/steps/step-*.log`) expose tool/file
behavior in a stable `key=value` format: `permission=<read|edit|bash> pattern=…`
(tool + target), `"shell tool using shell"` (shell), `agent=<mode>` (build /
plan / title / undefined). There is NO explicit skill-invocation event — skills
are injected into context, so file-based skill attribution is a lower bound
(only counts `SKILL.md` reads). `"touching file"` is the file-watcher and fires
on reads too, so it is NOT an edit signal — edits come only from
`permission=edit`.

**Why it matters:** these are the ground-truth signals for pillar 3 attribution;
misreading "touching file" as an edit wrongly flags read-only governance docs.
**How to apply:** count edits from `permission=edit`; treat skills_consulted as a
documented lower bound; parse step logs, fall back to opencode.log.

## SWE-bench generic runtime runs only a narrow slice — 2026-07-20

The image-free runtime (clone → `uv pip install -e .` on Python 3.11 → pytest the
F2P/P2P nodeids, ADR-0009) works for a *narrow* slice, proven via
`make swebench-selftest` (golden-patch, zero tokens). Three hard boundaries found
while curating SWE-bench Verified: (1) **Py3.11** — pre-3.11 code fails at import
(every `psf/requests` Verified instance vendors an old urllib3 doing
`from collections import Mapping`); prefer 2022+ releases. (2) **Standard pytest
nodeids only** — F2P ids with spaces/params like `test_x[NOT AND SELF]` don't
match under the generic runner (`pytest-10081/10356` fail; clean-nodeid
`pytest-10051` passes). (3) **pytest-collectable only** — `sympy` names tests by
bare function (`test_homomorphism`) for its own runner; `pylint` tests fail
collection. Reliable slice today: modern `flask` + `pytest` (7.x). Validated set
lives in `docs/swebench-curated-set.md`.

**Why it matters:** picking instances by "looks pure-Python" wastes runs; most
Verified repos (django/sympy/matplotlib/sklearn/pylint) don't run here.
**How to apply:** never trust an instance until `make swebench-selftest` passes;
`fetch_instance` searches both Lite+Verified (`swebench_client._DATASETS`); for
dataset-free objective tasks author a `build_from_scratch` case with exit-code
`validation_commands` (see `benchmarks/cli-textstats/`).

## Harness is an executable dimension via per-CLI adapters — 2026-07-20

The New-run `harness` field used to be cosmetic: the runner always called
opencode (`build_opencode_cmd`), and the runner image installs only opencode.
codex/claude are now real harnesses (`build_codex_cmd` / `build_claude_cmd`,
dispatched by `build_harness_cmd` in `bench_run.py`, threaded via `--harness`).
Key facts: the workspace path lives in the *prompt* (`workspace/<run_id>/`), so
all harnesses share `cwd=REPO_ROOT` — only the CLI differs. codex/claude run on
the **host** (their CLIs + native auth live there, not in the container), so they
require the subprocess backend; a `RunService._preflight` guard rejects them
under the docker backend. The gateway model dropdown (`gateway/…`) doesn't map to
native Codex/Claude ids, so `_native_model` ignores it → CLI default.

**Why it matters:** adding a harness = a builder + install location, not a whole
new runner; and codex/claude only work in host mode (make app-worker-host /
app-api-host), never under `make app-up`.
**How to apply:** see `docs/harness-adapters.md`; builders are unit-tested in
`tests/harness_lab_app/test_harness_adapters.py`; real headless completion on
tetris still needs first-run verification per environment.

## Continuous-improvement cycle: audit findings → GitHub issues — 2026-07-20

`scripts/lab_improve.py` (`make improve RUN=<id>`) closes the lab's feedback loop:
it reads a run's LLM audit (`audit.md` + `audit_findings.json`, verdicts
useful|redundant|improve|unknown), runs a *dedicated* LLM synthesis pass
(reusing `bench_audit.call_gateway`) to turn the "improve" signals into concrete
GitHub issue proposals, and files them via `gh` on the host. Two steps: default
dry-run writes an editable `data/runs/<id>/improvements.json` (+ `.md` preview);
`CREATE=1` files the issues (idempotent — created URLs are written back).
Approval = running the create step. Routing (`route_repo`, editable in the JSON):
skills/governance-docs/subagents/hooks → `IMPROVE_REPO_TEMPLATE`
(xmartlabs/sdlc-ml-python-base); process/harness-hint → `IMPROVE_REPO_HARNESS`
(xmartlabs/sdlc-harness-engineering-guide).

**Why it matters:** this is how lab experiments become durable improvements to
the template/harness repos, not one-off observations.
**How to apply:** needs an audit first (`make bench-audit` or Run audit button) +
`gh auth` + gateway; see `docs/improvement-cycle.md`; pure fns unit-tested in
`tests/harness_lab_app/test_lab_improve.py`.

## SWE-bench Pro lane: image-based hard cases — 2026-07-21

The Pro lane (`docs/design-swe-bench-pro-lane.md`, golden-patch-proven on an
ansible instance) has four non-obvious gotchas learned the hard way:

1. **Pro images default to a bash entrypoint** (upstream issue #6). Passing
   `bash -lc …` as CMD runs `bash bash …` and dies silently — always use
   `--entrypoint bash` and pass only `-lc <script>` as args.
2. **`dockerhub_tag` is not the real tag.** The published tag is *derived*
   (`helper_code/image_uri.py` port in `swebench_pro_client.image_uri`):
   `-vnan` stripped, element-web special-cased, truncated to 128 chars.
3. **Pro list columns are Python-repr strings**, not JSON (`['a', 'b']` with
   single quotes) — parse with `ast.literal_eval`, not `json.loads`.
4. **The gold patch rides the normalized spec for self-tests but must never
   reach `metadata_json`/`case_spec.json`** — that file is bind-mounted where
   the agent can read it (oracle mode leaks test_patch on purpose; the gold
   patch would void the bench).

**Why it matters:** silent zero-scores (gotcha 1) look identical to "agent
failed"; a wrong tag (2) looks like a missing image; and (4) is a validity
threat to every Pro result.
**How to apply:** provisioning/validation live in `scripts/swebench_pro_common.py`
(worker-side in the web lane: runner has no docker socket; validation routes via
the `local` flag in `build_validation_spec`); smoke with
`make swebench-pro-selftest INSTANCE=instance_…`. Images are amd64-only — dev
smokes on Apple Silicon run emulated; real benches belong on the amd64 server.

## Operating the lab: restarts kill in-flight runs — 2026-07-22

`docker compose restart worker` aborts any run mid-flight (the run is marked
failed with "Worker restarted while the run was in flight; retry to resume").
The worker is deliberately concurrency-1, so one active run is always possible.

**Why it matters:** deploying lab code changes while an experiment runs silently
costs a run (and its tokens).
**How to apply:** before restarting api/worker, check the runs list for
`running`/`pending` states (`GET /api/runs`) — restart only when empty. A run
killed this way is recoverable with the Retry button.

## LM Studio JIT auto-load serves the model's *default* context — 2026-08-10

The context length you set by hand in LM Studio applies to the model **you
loaded**. A request for a model that is not loaded triggers a JIT load using that
model's **default** window (often 8k) and ignores your setting. Nothing upstream
looks broken: LiteLLM answers normally and `/v1/models` lists the model.

The harness system prompt is **~13.7k tokens before the first user turn**, so
under an 8k window llama.cpp rejects every call *before evaluation* with
`n_keep: 13777 >= n_ctx: 8192` in ~0.4s. Run `tetris-v1-K-020` burned 4432 steps,
2.5 hours and a 12 MB log this way, and the failures surface as generic stream
errors.

Two related traps found alongside it. **Alias identity is the full parameter set,
not the target.** `/model/info` shows `lmstudio-qwen3.6-35b` and its `-build` twin
pointing at the same LM Studio model but with different sampling (0.7/top_p 0.8
against 0.2/top_p 0.9 + penalties + `max_tokens` 4096) — two real arms. Meanwhile
`claude-opus-5`, `-oauth` and `[1m]` are byte-identical in everything the gateway
exposes. Judge by params, never by name. **And LiteLLM returned an identical
`chatcmpl` id + `created` for two identical requests**, which means response
caching — poison for a benchmarking lab if it is on.

**Why it matters:** the symptom (opaque stream errors, instant failures) points at
the network or the harness, not at a context setting on a machine that reports
itself healthy.
**How to apply:** load the model explicitly with the window you want before a run,
or raise its default. The lab now measures the *served* window with a real
oversized prompt — Settings -> Model catalog -> **Check context**, and run
creation refuses a proven window under `MIN_CONTEXT_TOKENS` (32768). Both fail
open, so only a provable shortfall blocks. See
[ADR-0020](../docs/adr/0020-context-window-preflight-and-alias-merge.md) and the
"JIT auto-load trap" section in `docs/local-model-runtime-config.md`.

## A silent local model is prefilling, not stuck — 2026-08-13

Measured against the box serving `qwen/qwen3.6-35b-a3b`: a trivial prompt returns
its first token in **0.2s**, the harness's ~24k-token prompt with tool schemas in
**318s**. Time-to-first-token is dominated by prompt processing and scales with the
prompt, so a big local model produces *no output at all* for minutes at the start of
every turn while looking, from outside, exactly like a wedged one.

The lab had one 600s silence clock (`run_stall_seconds`, added for the hung-pygame
run `K-034`), which sat below that prefill time. Every 35B run therefore died at its
first turn — `K-040/049/050`, all at `message=stream` → `llm runtime selected` →
silence → exit 137 at exactly 600s — and was filed as the *agent* wedging the
harness. The 9B, whose worst gap was 76s, always passed. Event counts make the shape
obvious: 99/221/126 events for the 35B runs against 1727–3791 for the 9B.

Found alongside it: **opencode retries a failing stream forever.** `K-029` logged 123
consecutive `AI_APICallError: Cannot connect to API` over 70 minutes and was recorded
as `Run timed out after 7200s` — i.e. as nemotron attempting the case. That pattern
explains the block of eight 130–157 minute "timeouts" from `K-018` to `K-030`.

**Why it matters:** both defects blamed the model for a lab problem *and* fed the
leaderboard, so "the big model regressed" was an artifact of the harness. A
benchmark that mislabels its own failures is worse than one that records nothing,
because it looks authoritative.
**How to apply:** the watchdog now picks its budget from what the run was doing when
it went quiet — `run_model_wait_seconds` (2400) while a request is in flight,
`run_stall_seconds` (600) otherwise — and gives up after `run_max_stream_errors` (5)
back-to-back provider failures. Only agent-caused silence counts as an attempt. When
a local model looks hung, check GPU load before the harness: a prefilling model pins
it, a stuck tool call does not. See
[ADR-0022](../docs/adr/0022-watchdog-splits-model-wait-from-agent-stall.md).

## A green check on a Dependabot PR can mean "never tested"

`docs-quality-guardrails.yml` has two jobs: a Python gate (`make check`) and a
frontend gate (`make check-frontend`). **Neither builds a container.** So a
base-image PR — nginx, node, python — reports ✅ without a single line of it being
executed. The check is measuring the absence of a test, not the presence of a pass.

Building `Dockerfile.frontend.prod` by hand found a bug that had been live the whole
time and had nothing to do with any bump: the `HEALTHCHECK` probed
`http://localhost/`, which resolves to `::1` first inside the container, while
`deploy/nginx.conf` declares only `listen 80` (IPv4). Every probe got
`ECONNREFUSED`, so the production frontend container reported `unhealthy` for its
entire life while serving traffic perfectly. It reproduces identically on
`nginx:1.27-alpine`, so it predates the upgrade — CI simply had no way to see it.

The same sweep showed Dependabot's one-major-per-PR split creating *unmergeable*
PRs: `@vitejs/plugin-react@6` requires `vite@^8` and `vite@8` breaks
`plugin-react@4`, so each PR fails `npm ci` alone and only passes together. Five
frontend PRs also share one `package-lock.json`. Merging them one at a time is not
a slower path to the same place — for two of them it is not a path at all.

**Why it matters:** dependency PRs are where review attention is thinnest and the
green check does the most work. When the gate does not cover the artifact being
changed, the check is decoration, and the temptation is to batch-merge on it. The
upgrade was also genuinely security-relevant — `npm audit` went from 4 findings
(2 high on postcss/nanoid, 2 moderate on react-router including an open-redirect
CVE bypass) to 0 — so the cost of getting it wrong was real in both directions.

**How to apply:** before merging anything that changes a Dockerfile, build the image
and exercise it (`/`, a client-side route, a hashed asset, and the healthcheck) —
`make check` will not. When two Dependabot PRs fail on each other's peer ranges,
stop trying to sequence them and replace the set with one verified branch. Base
images now move only with the gate that verifies them, enforced by `ignore` rules in
`.github/dependabot.yml`; see
[ADR-0024](../docs/adr/0024-base-images-track-ci-verified-toolchains.md).

## Continuing a run: a child run was the cheap design, and two bugs sat next to it

**What happened:** adding "continue a finished run with a new prompt" looked like
it needed runner support — step offsets, merged usage, re-opened artifacts. It
needed none. Modelling the continuation as a *new run* whose workspace is copied
from the parent means it owns its own `data/runs/<id>/`, so there is nothing to
offset or merge. Two flags that already existed did the rest: `--resume` makes
`validate_workspace` keep the seeded tree, and `provision_workspace`
short-circuits on an existing `.git`, so a rehydrated SWE workspace is not
re-cloned. The only genuinely new code was one `shutil.copytree` and a prompt
header telling the agent the inherited workspace is its own.

The one place the lane mattered: a curated case (`RUNS_FILES`) resolves its
prompt from `benchmarks/<case>/`, ignoring `case_spec.json` entirely — so a
continuation had to override `_prompt_source.md` *after* the lane branch, not
inside the DB branch.

Two adjacent defects surfaced while wiring it, both in the same shape — a
`RunConfigCreate` rebuilt field by field, silently dropping the fields added
later:

- `tasks._config_payload` omitted `skills_disabled`/`skills_extra`, and
  `execute_run` overwrites `run.command` with what it rebuilds. **The skills
  ablation never reached the container.** The stored preview command had the
  `--disable-skill` flags; the executed one did not. Every ablation arm run
  before 2026-08-24 measured the full harness.
- `_resume_config` (retry) and `clone_run` dropped the same two fields, so a
  retry or clone quietly changed arms.

**Why it matters:** ADR-0011's whole point is that an ablation is a distinct
condition. A silent drop does not fail — it produces a plausible number for a
condition that was never run, which is worse than an error.

**How to apply:** when a feature can be expressed as a new row rather than a
mutation of an existing one, price that first — immutable artifacts make most of
the hard parts disappear. And treat every hand-rolled `RunConfigCreate(...)` as a
place a future field will be dropped: `_config_payload`, `_resume_config`,
`clone_run` and `continue_run` must all be updated together when the schema
grows. See [ADR-0026](../docs/adr/0026-run-continuation-as-child-runs.md) and
[guide-run-continuation.md](../docs/guide-run-continuation.md).

## claude stream-json per-turn usage undercounts output (2026-08-27)

In `steps/step-N.events.jsonl`, each assistant message's `usage.output_tokens`
is a streaming snapshot taken before the message finished: summing them across
a run gives ~5% of the real total (`1348` vs `26987` on `tetris-v1-K-004`).
`input_tokens` and the cache counters per turn are usable; output totals must
come from the terminal `result` event or `usage.json`. The events also carry
no timestamps of their own — `bench_run.stamp_event_line` now injects
`_received_at` at capture time, and the Langfuse exporter labels anything
older `timing_source: interpolated`. Also: `run_id` is not globally unique
across copies of a run directory, which is why OTEL ids are salted with
`condition_hash`. See
[ADR-0028](../docs/adr/0028-step-traces-carry-per-call-detail.md).

## A subscription token is a whole LLM transport (2026-09-04)

The lab made four independent `POST /chat/completions` calls — the auditor, the
SWE judge, the DeepEval judge, the case summariser — each with its own copy of
the same twelve lines. Collapsing them into `llm_transport` was worth doing on
its own, but the reason it mattered is that **one seam is one place to add a
second transport**: `claude -p --output-format json --tools "" --strict-mcp-config`
is a text-completion endpoint, so a teammate whose only credential is a Claude
subscription now gets audits, judges and summaries with no paid API key and no
gateway. `--tools ""` is the load-bearing flag: it makes the CLI a completion
rather than an agent, so no permission prompt can ever block a worker.

Three things that were not obvious until the code ran:

- **The CLI reports failure in-band.** A bad model name comes back as exit 1
  *plus* `"is_error": true` *plus* the error text sitting in `result`, exactly
  where an answer would be. A parser that reads `result` without checking the
  other two returns an error message as if it were an audit.
- **The audit prompt must go on stdin.** The real one measured 86 KB (clipped
  diff plus harness log). Not over the argv limit, but close enough that a
  larger diff would be.
- **Model names are transport-specific**, so defaults have to be too.
  `gpt-5.4-mini` means nothing to the CLI and `claude-sonnet-5` means nothing to
  a gateway that does not proxy it; a name that is not a Claude id is dropped so
  the CLI picks its own default instead of sending a guaranteed 404.

**Why it matters:** the ordering is the entire compatibility promise. The gateway
is checked first, always, so adding a Claude credential to a multi-model lab can
never move a judge — a test asserts it. Two labs judging the same run over
different transports produce numbers that are only loosely comparable, which is
why each judged score records its transport in provenance.

**How to apply:** when a capability is duplicated more than twice, the third
copy is the signal to extract a seam — the payoff is usually the *next* feature,
not the deduplication. And validate a fallback path end to end before believing
it: this one uncovered `ToolCorrectnessMetric()` being constructed outside its
`try`, where DeepEval eagerly builds an OpenAI client and raises on a missing
`OPENAI_API_KEY`, killing all three trajectory metrics on any lab without that
key. Unit tests never saw it; a real subscription-only run did. See
[ADR-0043](../docs/adr/0043-one-llm-transport-gateway-first-claude-cli-second.md)
and [the plan](../docs/plans/2026-09-claude-native-llm-transport.md).

## A timeout is a fact about the lab, not about the agent (2026-09-08)

Four Tetris runs — all four working games — scored 1/10. Nothing in the chain was
about the code:

- **`timeout` alone cannot kill an SDL program.** Plain `timeout <N>` sends SIGTERM;
  SDL converts SIGTERM into an `SDL_QUIT` *event*, which an interactive loop is free
  to ignore. The command wedged, exited 124, and `passed = exit_code == 0` read that
  as "the code fails". Always `timeout -k <grace> <N>`.
- **A `-k` escalation is not enough on its own.** The wedge can sit anywhere, and
  `RunHandle` (executors.py) has no deadline: `wait()` takes no timeout and
  `iter_lines()` is the blocking call. The only shape that works is a
  `threading.Timer` watchdog calling `handle.stop()`, sized from the command's own
  `timeout` budget.
- **"0/1 checks passed" is the worst possible sentence to hand a judge.** It reads
  identically whether an assertion returned the wrong number or the process was
  killed before it could answer — and the judge, which *does* read the diff
  (`judge.py:build_rubric_message`), had no reason to distrust it. Passing the full
  per-check list with an explicit INCONCLUSIVE marker costs nothing and removes the
  ambiguity.

**Why it matters:** two-valued validation forces every unknown into "failed", and a
false zero is worse than a missing one — it averages into `success_rate` and looks
like a measured result. This is ADR-0046's *infrastructure entering the corpus as
capability*, one layer below the crashed harness it was written about.

**How to apply:** when a boolean summarizes a process that can be interrupted, check
whether it can also express "did not run". Then propagate that third value all the
way out — evaluator (exclude, do not zero), judge prompt (refutes nothing), UI (warn,
not error) — because any layer that flattens it back to false undoes the other three.
See [ADR-0048](../docs/adr/0048-a-check-that-did-not-measure-is-not-a-failure.md).

## A badly formed case reports a number about itself (2026-09-09)

Two cases written from the talos audit ran four experiment arms and produced nothing
about talos, and nothing about the lab's own template either. Every defect was silent:

- **A shell `for` loop exits with its last command's status.**
  `for t in {workspace}/tests/test_*.sh; do bash "$t"; done` reported
  `task_validation_pass = 1` while the same run's stored detail read
  `rc=2 CONFIG ERROR`. Fifty-four failures ahead of one pass is a green check. The
  fix is `|| exit 1` **inside** the loop body.
- **A repo-relative path with no `{workspace}` resolves against the lab's own root.**
  `bash benchmark/model/run-model.sh` exited 127 in both arms. The file existed; the
  path did not. Exit 127 in a validation check is almost always this.
- **`source: "git_target"` is not a case type.** `bench_run.py` decided "is this
  SWE-bench?" by calling `spec_provisions_repo()`, which answers *"does a repository
  have to be cloned?"* — true for **every** case paired with an external target. So
  from step 2 both talos cases were told to resolve a GitHub issue, shown an empty
  list of target failing tests, and told to run `python -m pytest` in a repository
  with no Python. Both arms. The injected arm reacted worse (6.6× turns, 9× cost,
  zero diffs), which would have been reported as a treatment effect.
- **A run parked in `awaiting_manual_score` with an empty rubric is a case defect, not
  a scoring backlog.** `_llm_judge_rubric` returns at `if not rubric` — silently — and
  the run looks like something a human just has not got to.
- **Runner capabilities include POSIX tools, not only interpreters.** Every talos test
  died because the image shipped no `jq`, and the agent's own `apt-get install jq`
  lived in the *agent's* container, which the later validation container does not
  inherit.
- **A repository's harness may not be in a dotted directory.** talos keeps sixteen
  skills in `skills/` and four hooks in `hooks/`; four drifted path taxonomies all
  named only `.claude/`-style roots, so the audit reported "no harness at all" — worse
  than "unknown", because it reads as a finding.

**Why it matters:** none of these show up as an error. A false green occupies the same
field, in the same colour, as a real one, and it averages into every aggregate above
it. The lab was shaping every result it produced, on a repository it had audited once —
and the whole point is to audit repositories it has never seen.

**How to apply:** when a check is written, ask what would make it fail, and then make
the lab ask the same thing — statically for the shapes that are always wrong, and
empirically by running the check against the clean tree before any agent touches it. A
check that passes on the untouched repository has told you nothing. And when one
predicate is used to answer two questions, name the second one: the SWE-bench leak was
a single `elif` reusing a function whose docstring already said it answered something
else. See [ADR-0057](../docs/adr/0057-a-case-is-validated-before-it-can-measure-anything.md)
and [the case-formation guide](../docs/guide-case-formation.md).

## A source edit is not a deploy: api and worker do not reload (2026-09-10)

The wizard kept proposing `bash tests/test_*.sh` and refusing to save it after the
fix that repairs exactly that hint had landed. Nothing was wrong with the fix. The
`api` (uvicorn, no `--reload`) and `worker` (celery) containers bind-mount `.:/app`,
so the files on disk were current — and the *processes* were seven minutes older
than the edit. `docker compose exec worker python -c "import …"` is no help here:
it starts a fresh interpreter that reads the disk, not the memory of PID 1.
Compare `/proc/1` start time with the source mtime instead.

**Why it matters:** two rounds of the same defect look like the fix does not work,
and "do we have to iterate like this on every repository?" is the right question
to ask when a lab keeps contradicting itself. The answer was operational, not a
design gap — but it read as one.

**How to apply:** after editing anything under `src/`, `docker compose restart api
worker` (the runner image needs `docker compose build runner` only when
`Dockerfile.runner` changes). A stored audit keeps the case plan the old worker
computed; re-run the audit once after restarting. Do not read a stale process as a
failed fix.

## A union is right for a batch and wrong for an experiment (2026-09-10)

The New evaluation wizard built its prompt pickers from the union of the selected
cases' variants, copying batch mode, where `_resolve_variants` legitimately drops the
case/variant pairs a case cannot run. An experiment cannot drop them: it runs one
declared design across every case it selected, and `ExperimentService.launch` refuses
the whole launch if any case lacks the variant an arm asks for. Selecting one lab case
(`default`/`harness`) beside three SWE-bench instances (`swe_default`) therefore put a
prompt in the pickers that three quarters of the design could not run.

**Why it matters:** the refusal read as an id problem. Creation and launch are two
calls, so the refused design was already persisted, and the retry — same question, same
derived id — was filed under a suffixed one. Every attempt cost a row and a slug, and
the symptom pointed away from the cause.

**How to apply:** when copying a rule from the batch path into the experiment path, ask
whether the batch's freedom to drop a combination survives. Where it does not, the
picker offers the intersection. And validate a design at creation for everything
creation can already see — a refusal after the row exists is a refusal that leaves
litter. See [ADR-0059](../docs/adr/0059-a-design-that-cannot-launch-is-not-created.md).

## A clone is a repository root, and Claude Code stops there (2026-09-12)

Claude Code loads `.claude/skills/` from the cwd and its parents *up to the nearest
repository root*. The SWE lane starts the claude agent inside the workspace, which is
a git clone — so the walk ends at the clone and never reaches `/app/.claude/skills`.
`CLAUDE.md` is collected from every ancestor regardless, which is why the agent read
a rulebook naming twenty-four skills and was told `Unknown skill` on every call. Five
runs, five resolved instances, zero real skill activations, and one arm scored
`skill_invoked = 1.0` because the parser counted the rejected calls.

**Why it matters:** every skill-activation number for a `claude` + `lab_root` run
started in a clone was measuring the CLI's discovery rule, not the agent. The
experiment audit called the design degraded for four true reasons and missed the
one that mattered, because it can see the arms but not the operator's intent.

An added directory is indexed and watched, so `--add-dir /app` (4.7 GB, 141k files,
and it contains the workspace the agent runs in) hung the CLI before session init:
three runs, zero-byte event streams, killed by the stall watchdog at ten minutes with
`exit code 137`. A 352 KB per-run staging directory holding only the catalog reaches
init in ten seconds.

**How to apply:** when a `Skill` call fails, look at the `system/init` event's
`skills` list before reading any score; if the governed names are absent, the run
measured nothing about them. The runner now passes `--add-dir <lab root>` for that
lane, and both parsers skip `tool_use` blocks whose `tool_result` is an error. When
an ablation selects nearly the whole catalog, the skill left out is the one held
constant — check the wizard's design note before launching. See
[ADR-0061](../docs/adr/0061-a-claude-run-started-inside-a-clone-must-be-told-where-the-skills-are.md).

## A gate runs where you put it, and a floor is as blind as a ceiling (2026-09-12)

`audit-ml-python-base` reported `0/2 checks passed` on all eight runs, validity
`valid`, verdict "No difference observed", and an LLM audit concluding the harness did
not help. Nothing was measured. A target repository's `validation_commands` replace
the case's (ADR-0029) but run with `working_dir="/app"`, so the target's `make test`
ran the **lab's** Makefile; and the `validate` phase is `network_mode="none"` by
design, so `uv` died on DNS. Both were stored as conclusive failures, both arms landed
at 0 %, and `success_delta` returned a confident `0.0`. Running the *cases'* own
checks by hand against the archived workspaces gave native 4/4, injected 3/4.

**Why it matters:** three independent layers agreed on a wrong answer — the runner,
the scorer and the auditor — because each was locally correct. The gate-check lane had
had the `cd {workspace} &&` prefix all along; objective validation never got the same
line, and nothing compared the two.

The same blindness existed one layer up: `health_at_pin` called that gate `fail`, so
the audit published "Suite al pin: **roja**" about a client repository whose suite it
had simply been unable to run. The auditor *model* read the DNS line in the tail and
corrected it in prose — a heuristic that is less careful than the text it feeds is a
heuristic waiting to be believed.

**How to apply:** when every arm of an experiment reads 0, look at one check's
`detail` before reading any delta — an `Error 127` or a DNS line means the sandbox,
not the agent. A validity engine that detects a ceiling needs the floor too, and it
must be gated on evidence (`unmeasured > 0`) rather than absence, or a freshly
launched experiment renders as invalid while its runs are still queued. Fix 2 of
[ADR-0062](../docs/adr/0062-a-check-the-environment-failed-is-not-a-verdict.md) is
stored at validation time, so old runs need **re-validating** before the new reading
applies — unlike the validity rules, which are computed on read.

## A gate runs where you put it, and a floor is as blind as a ceiling (2026-09-12)

`audit-ml-python-base` reported `0/2 checks passed` on all eight runs, validity
`valid`, verdict "No difference observed", and an LLM audit concluding the harness did
not help. Nothing was measured. A target repository's `validation_commands` replace
the case's (ADR-0029) but run with `working_dir="/app"`, so the target's `make test`
ran the **lab's** Makefile; and the `validate` phase is `network_mode="none"` by
design, so `uv` died on DNS. Both were stored as conclusive failures, both arms landed
at 0 %, and `success_delta` returned a confident `0.0`. Running the *cases'* own
checks by hand against the archived workspaces gave native 4/4, injected 3/4.

**Why it matters:** three independent layers agreed on a wrong answer — the runner,
the scorer and the auditor — because each was locally correct. The gate-check lane had
had the `cd {workspace} &&` prefix all along; objective validation never got the same
line, and nothing compared the two.

The same blindness existed one layer up: `health_at_pin` called that gate `fail`, so
the audit published "Suite al pin: **roja**" about a client repository whose suite it
had simply been unable to run. The auditor *model* read the DNS line in the tail and
corrected it in prose — a heuristic that is less careful than the text it feeds is a
heuristic waiting to be believed.

**How to apply:** when every arm of an experiment reads 0, look at one check's
`detail` before reading any delta — an `Error 127` or a DNS line means the sandbox,
not the agent. A validity engine that detects a ceiling needs the floor too, and it
must be gated on evidence (`unmeasured > 0`) rather than absence, or a freshly
launched experiment renders as invalid while its runs are still queued. Fix 2 of
[ADR-0062](../docs/adr/0062-a-check-the-environment-failed-is-not-a-verdict.md) is
stored at validation time, so old runs need **re-validating** before the new reading
applies — unlike the validity rules, which are computed on read.

## 2026-10-21 — `git -C <dir>` answers for the enclosing repo when `<dir>` has no `.git`

`workspace_fingerprint` hashed `git status` + `git diff HEAD` run with `-C workspace`.
A build-from-scratch workspace has no `.git`, so git walked up to the lab repo, where
`/workspace/` is ignored, and returned empty output on every step: the fingerprint was
the SHA-256 of the empty string and 36 runs stopped `no_progress` at step 2. The
existing test only covered a workspace that *is* a repo and a path that does not exist
— never the real layout, a non-repo nested inside an ignoring repo.

In the same pass, `dict.update` merged usage sources so a remote `cost_usd: None`
erased the cost the claude events had reported (37 runs, $56.09 unrecorded — more than
everything the lab had recorded).

**How to apply:** before trusting a change detector or a merge, test it on the layout
production actually has, not the convenient one. An "empty" result from git inside a
nested directory means "not my repo", not "no changes". When merging sources by
priority, a missing value must never beat a present one.

## 2026-10-21 — Calibrate every grader against a real artifact before trusting it

Three graders built this day looked right in unit tests and were wrong on contact
with reality, each caught only by running them on something real:

- **Harness checks on real repos** (ADR-0065). Symlinked skill projections read
  through `git show` return the link target, so 19 "skills without frontmatter" were
  projections, not skills; `command -v uv || echo …` was flagged as a swallowed test;
  `uvx mcp-server-git` and `sh -c "npx …"` passed as "pinned". Running
  `make harness-check` on five repositories found all of them.
- **A browser test suite** (ADR-0067). `get_by_role("button", name="Completed")` also
  matched *Clear completed*, so a correct page failed. Only running a reference
  implementation exposed it.
- **The factorial estimate** (ADR-0066). A case-weighted point beside pooled counts
  and a pooled Newcombe floor printed "33%" next to "1/21"; a bootstrap over one
  unanimous case read as certainty; the read order of conditions (control first, then
  by label) flipped the interaction's sign.

**How to apply:** a grader is not done until it has scored an empty input (must
fail), a known-good input (must pass) and a plausible half-right one (must land in
between). Keep one estimand per number shown together. Read untrusted repository text
with linear scans, bounded and flattened, never a backtracking regex. When a test DB
copy misbehaves, delete its `-wal`/`-shm` before copying over it — a stale WAL replays
onto the new file and reads as corruption.

## 2026-10-21 — A search over a set must not depend on the set's order

`_is_nested_ladder` decided whether three harness levels form a ladder by sorting
them on a key with ties and walking a `set`: the same conditions were a valid grid on
one `PYTHONHASHSEED` and refused on another. It now tries every ordering of the
levels (3–6 rungs, so at most 720) over a `repr`-sorted list.

**How to apply:** any check that walks a `set` or a sort with ties must give the same
answer for every order — run its test under `PYTHONHASHSEED=1..3`. And a reading that
cannot be established yet (a tier with too few cases) must say *why* on the page, not
only in a tooltip: "none yet" on every row read as "the model failed".

## 2026-10-23 — A warning in one form is not a guard

The harness x model form marked `claude-haiku-5-5` "not on your gateway"; New
evaluation did not, and the backend preflight for the `claude` harness checked only
that a token existed. A team member launched it from New evaluation: eight runs died
in under a second on `Invalid model name` and were listed as "agent error".

**How to apply:** a condition that makes a run impossible belongs in
`RunService.preflight`, which every launch path shares — the forms only explain it.
And a failure the model never got to cause must be labelled as the lab's, or the
run list and the leaderboard read infrastructure as capability.

## 2026-10-24 — Run the grader where it will run, before reading any result

Twelve runs of `eda-ml-iris-categ-numerico` scored 0–3 and looked like weak models. The
grader's container had no pandas (ten collection errors), the grade was the agent's own
test file, and the fix itself carried two bugs the unit tests could not see: the runner's
venv has no pip (uv only), and every graded command passed a lab-relative `{workspace}`
that stopped resolving after its own `cd` — the clinic cases had worked only because
`python -m pytest` puts the cwd on `sys.path`.

**How to apply:** before trusting a case or a validation change, run it through
`DockerRunExecutor` with `hardened=True` on an archived workspace — setup with network,
checks without — and calibrate the tests on a reference, an empty and a partial
workspace. When a whole grid fails the same way, suspect the grader first.

## 2026-11-06 — Test the statistics against a known answer, not against themselves

The unit tests of `statistics.py` checked that intervals contain their own point
estimate and are reproducible. They never checked how often an interval contains the
*truth*. A Monte Carlo review (simulated designs with a known effect, run through the
unmodified functions) found what no unit test could. The power planner promised 80%
and delivered 32–72%. The delta interval covered 83% at 4 cases × 24 repetitions with
an uneven effect. One arm's Wilson interval covered 55–58% of the population rate.
The same review found the Claude CLI transport silently ignored the requested
temperature and swapped the model, while the lab recorded the request.

**How to apply:** before claiming an interval or a plan has a property (coverage,
power, "errs on the side of more"), simulate it: draw per-case rates, apply a known
effect (even and uneven), call the real function, and count. About 200 draws per row
is enough to tell 95% from 85%. Record what an instrument actually did (the
effective model from `modelUsage`, temperature unknown on the CLI), never what it was
asked to do. ADR-0073.

## 2026-11-09 — LM Studio adds an instance per load; read back what a server served

- `POST /api/v1/models/load` on a model that is already loaded adds a second instance
  (`model:2`). It does not reconfigure the first. Two test loads left three copies of a
  27B model in VRAM. Before loading, check `loaded_instances`, and reuse or replace.
- LM Studio may serve settings nobody asked for (`parallel: 4`). Always read back
  `loaded_instances[].config` or `echo_load_config` instead of trusting the request.
- llama.cpp's `n_ctx` is shared across parallel slots, so KV memory does not scale with
  `parallel`.
- Hybrid (Qwen3.5+) and sliding-window (Gemma, gpt-oss) architectures keep KV on only
  some layers. A size-only KV formula overstates them about twofold.
- *Check configuration* through the gateway sends real completions and can JIT-load a
  22 GB model next to the one an experiment uses (2026-11-07). The local-tuning host
  guard now asks before that happens.

## 2026-11-09 — Speed tests must defeat the prompt cache; tg is bandwidth-bound

- llama.cpp (LM Studio, Ollama) reuses the longest cached prefix of a prompt. A
  deterministic filler made every larger test prompt an extension of the previous one.
  Prefill read 1,100–2,100 tok/s, rising with size; with a unique random tag per prompt
  it is a flat ~1,200 tok/s (bonsai-27b on an RTX 3090). A repeated prompt "read" at
  113,000 tok/s. Seconds per agent step moved from 7.3 to 11.6.
- Token generation is bound by memory bandwidth: `tok/s ≈ GB/s ÷ GB read per token`.
  Published llama-bench results reach 64–81 % of that ceiling on consumer NVIDIA GPUs
  (llama.cpp discussions #15013, #4167). 1-bit quants (bonsai Q1_0: 37 %) are
  compute-bound, so the ceiling does not apply to them.
- Count prompt tokens from the server's `usage.prompt_tokens`, not the requested size:
  the tokenizer decides.

## 2026-11-09 — Frontend id types must match the backend's integer ids

- SQLModel tables return integer `id`s, but `frontend/src/types.ts` typed the local
  machine/profile ids as `string`. A select value parsed back with `slice()` compared
  `"1" === 1`, so picking a saved machine silently did nothing — and because test
  history is saved only when a machine is selected, every test went unpersisted.
  Type ids as `number` so `tsc` flags string/number comparisons.
- LM Studio's REST API neither takes nor echoes the KV cache quantization (K/V Cache
  Quantization in its load panel). The lab can only record the type the user selected;
  a reliable quantized-KV test is a manual load plus evaluate-only mode.

## 2026-11-13 — Nothing committed is hidden from the agent; Godot headless never draws

- A targetless workspace (`workspace/<run_id>/`) has no `.git` of its own. The runner
  uses the lab's `/app/.git` for per-step snapshots, so the agent can read any committed
  file with `git show HEAD:…`, however its working-tree copy is masked. Held-out grading
  data must be **generated per run** (seeded with `{run_id}`), never stored and hidden
  (ADR-0078). A `hidden_paths` tmpfs mask was built and removed for this reason.
- Godot 4.7 under `--headless` never fires `RenderingServer.frame_post_draw`: a grader
  awaiting it hangs forever. Pixel or screenshot work needs Xvfb + Mesa llvmpipe
  (`godot-render` in `harness-lab-runner-godot`). Logic, autoloads, injected
  `InputEventMouseButton` and threads all work headless.
- A `class_name` is unknown to `--script` before the project's first `--import`; load
  helper scripts by path (`preload("res://rules.gd")`).
- A script error inside the agent's GDScript does not stop Godot: the call logs and
  returns the default. A harvester can call a broken contract and keep going.
- Graders written against a probe's literal FEN fail correct engines: a FEN may name
  an en passant square no capture can use, which every FEN writer (and python-chess)
  drops. Compare normalised positions. Calibration on a reference caught this and a
  wrong light/dark square choice — both grader bugs, not engine bugs.

<!-- Add new learnings above this line -->

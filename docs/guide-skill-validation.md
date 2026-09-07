# Guide: validating a skill with the lab

How to answer "does this skill actually help?" with runs instead of opinions.
Two lanes share the same machinery (ADR-0011): **ablate** an existing governed
skill, or **trial** a brand-new staged skill.

> **Two different questions live here, and they are not interchangeable.**
> Lanes A and B ask about the **harness**: does adding or removing this skill
> change the outcome? That needs an ablated arm, and it is what
> `harness_evaluation` licenses. Lane C asks about a **subject you built** — an
> agent behind an API, an MCP server, a service wrapper — driven by a skill:
> does it work reliably across harnesses and models? Nothing is ablated there,
> so it says nothing about the harness, and it runs under its own mode,
> `subject_validation` ([ADR-0040](adr/0040-subject-validation-mode.md)). Pick
> the lane by the question, not by the machinery — the machinery is the same.

## The core idea

A run's `condition_hash` identifies its experimental arm (harness + model +
prompt + case, ADR-0003). Disabling a skill — or adding a staged one — flips
the hash, so "with" and "without" runs are distinct arms you can compare on the
multidimensional score (ADR-0004), the attribution panel (ADR-0005), and the
LLM audit's per-item verdicts (useful / redundant / improve).

Local models at temp>0 are statistical: run **N repetitions per arm** (3+ is a
reasonable start) and compare distributions, not single runs.

## Lane A — ablate an existing skill

1. Open **Evaluate → New evaluation** and pick the **Remove skills** factor:
   the control arm keeps the skill(s), the treatment arm runs without them, and
   everything else stays identical. Launch it with repetitions. For a single
   ablated run instead, **Advanced → Runs → New run**, expand the **Skills**
   section, uncheck the skill(s) under test and create the run; create the
   baseline with all skills checked (same case, model, prompt, max steps).
2. During an ablated run the runner regenerates the native skill views
   (`.claude/skills/` and the adapter-file skill blocks) without the skill and
   restores them afterwards (`git status` must be clean once the run ends). A
   run container sees exactly one catalog, `.claude/skills/`; the other native
   catalogs are masked (see [harness-architecture.md](harness-architecture.md)).
3. Compare the arms in the experiment detail or Compare: objective validation, tokens,
   steps, attribution ("consulted"), audit verdicts.

## Lane B — trial a new (staged) skill

1. Get the skill into staging. Three ways, all from the browser:
   - **Skills → Import a skill** — paste whatever the source gave you:
     - a GitHub URL — repo, subdirectory (`/tree/main/skills/my-skill`) or a
       `blob` link;
     - a skills.sh page — `https://skills.sh/owner/repo/my-skill`;
     - an install command — `npx skills add <repo> --skill <name>` (also
       `bunx`, `pnpm dlx`, `git clone`, `gh repo clone`);
     - the `owner/repo` shorthand, a raw `SKILL.md`, or an archive URL.

     Optionally pin a ref. Home pages and installer scripts (`install.sh`) are
     plugin installers, not skills — those are refused before anything is
     fetched.
   - **Upload** a `.md`, `.zip` or `.tar.gz` from the same panel.
   - **New staged skill** — write the `SKILL.md` by hand (frontmatter `name:` +
     `description:`, then the workflow body).

   Import and upload are **two-step**: you first get a preview (file list,
   detected name, warnings, total size) and nothing is written until you press
   **Import**. Re-importing over an existing skill requires ticking *Replace*.

   A source holding several skills — a catalog repo such as
   `github.com/anthropics/skills` — answers with a **picker** instead of
   guessing: click the one you want and the preview reloads for it. Naming the
   skill up front (in the URL path, or via `--skill <name>`) skips the picker.
2. The skill is stored in the DB — every file, not just `SKILL.md` — and
   materialized at `data/skills_staging/<name>/` (git-ignored). The governed
   catalog is untouched. Multi-file skills keep their `scripts/`,
   `references/` and any other supporting files.
3. Compare it against the harness. **New evaluation → Evaluate a harness
   change → factor "Add skills"**, tick the staged skill, pick the cases and the
   repetitions. The design gets two arms with the same harness, model and case:
   - **baseline** is *the governed harness as it stands*: `CLAUDE.md` plus every
     governed skill under `.github/skills*` (about 25 today), with the staged
     skill absent. It is not a bare model and not a skill-less agent.
   - **with-skills** is the same harness with the staged skill projected into
     the agent's native skill view, next to the governed ones.

   **Available is not invoked.** Adding a skill puts it on the agent's menu; the
   agent reads it only when the case's instruction or the skill's own trigger
   phrases call for it. A skill whose `description` says "use for `/caveman`,
   'be brief'" will sit unused on a case that never says so, and the experiment
   will honestly report *no difference* — about a treatment that never
   happened (ADR-0041). Two different questions hide here; pick one per
   experiment:

   - **Does it help when asked for?** Tick **Enforce skill use** under the
     skill list. The arm that has the skills is then told, at the start of
     every step, to invoke them and follow them; the case itself is not
     touched, so both arms still run the same instruction. This is the
     question for one specific skill, and the usual one.
   - **Does the agent reach for it unasked?** Leave the box off. The skill is
     only available, and the interesting observation is which skills the
     agent opened, and why — the question for a whole harness of skills, where
     forcing every one would be an unnecessary invocation.

   Either way the run records it: `skill_invoked` (share of the skills under
   test the agent actually opened — a `Skill` call or a read of `SKILL.md` in
   the step record) and `skill_invocations` (how many times) appear in the
   metric table, and an arm at zero puts a caveat at the top of the verdict:
   *this arm measured the skill's availability, not its use.* Read that
   before the delta. To make an unasked skill trigger on the task, rewrite
   its `description`; every edit is a new `condition_hash`.

   For a one-off, **Advanced → Runs → New run → Skills → Experimental** launches a single
   run with the staged skill; launch the baseline without it.
4. Iterate: edit any file from **Skills → Edit** (file list on the left, editor
   on the right; add and delete files there too) and re-run. Every edit flips
   the `condition_hash` — the digest covers the **whole tree**, so changing a
   helper script is a new arm, not a silent reuse of the old one.
5. When it earns its keep, press **Promote**: the whole tree is written to
   `.github/skills-external/<name>/` and `make sync-skills` runs.
   **Nothing is committed** — review `git status` and commit yourself.
6. If it doesn't earn its keep: Archive (kept for the record) or Delete.

> An imported skill is text an agent will read and scripts it may run. The
> import guards (https-only, no private addresses, no archive traversal, size
> caps) protect *this host* while fetching; they say nothing about what the
> skill instructs. Read the preview. See ADR-0017.

## Lane C — validate a subject you built

Use this when the skill is not the thing under test: it is the driver for
something you wrote — an agent served behind a local API, an MCP server, an
internal service, a wrapper. The question is whether that subject does its job
reliably when a real agent drives it, under more than one harness or model.

Start from **Home → Validate your tool** (`/evaluate/subject`). It is a wizard
with gates, and each gate exists because skipping it produces a number that
cannot be read.

1. **Bring your tool.** Stage the skill that drives your subject — import,
   upload, or write it in the browser (same staging as Lane B). If the skill is
   already promoted into the governed harness, say so explicitly instead of
   staging a copy. *Ask the agent to use the selected skill(s)* is on by
   default here: this mode exists to check that one specific skill drives one
   specific tool, and a skill that is merely available is opened only when the
   instruction or its own triggers call for it (ADR-0041). Turn it off to see
   whether the agent reaches for it unasked. The runs record `skill_invoked`
   either way, and the verdict warns when the driver was never opened.
2. **Declare what success is.** Name the subject, write the instruction the
   agent gets, and declare **at least one executable validation command**. This
   gate is a hard block, in the client and again in the API: a case whose only
   "validation" is prose produces zero commands, so `task_validation_pass` is
   never emitted and the pass rate comes back empty *after* the runs have been
   paid for.
   **The gate can force a command to exist; it cannot force it to be able to
   fail.** A smoke check that the game starts cannot fail once the game starts.
   Write the command that would go red if the subject were broken.

   **One shell command per line.** Each entry is run verbatim with `sh -c` in
   the archived workspace, and the wizard's textarea is split on newlines when
   the case is saved. A heredoc or a multi-line script therefore never survives
   as a unit: its first line runs alone (`node - <<'EOF'` with an empty stdin
   exits 0 — a pass that measures nothing) and the rest is dropped line by line.
   Inline multi-statement checks instead:

   ```sh
   pytest {workspace}/test_snake.py -q
   node -e "const m=require('{workspace}/snake.js'); if(typeof m.step!=='function') process.exit(1)"
   python3 -c "import sys; h=open('{workspace}/index.html').read().lower(); sys.exit(0 if '<canvas' in h else 1)"
   ```

   **Draft it with AI (optional).** The assistant on this step turns a plain
   description plus the selected skills' `SKILL.md` into a subject, an
   instruction and validation commands. It is held to the same rule: the server
   screens every command it returns with the runner's own executability check,
   refuses multi-line entries, gives the model one corrective turn if nothing
   runnable is left, and returns what it dropped and why. The wizard lists those
   under the draft so the gap is visible before the case exists, not after the
   runs have spent their budget. The assistant never weakens the gate: an empty
   draft still blocks "Create the case".
3. **Choose the arms.** One arm per harness/model cell, labelled
   `<harness>/<model>`. There is no control role — every arm is a peer. Set the
   repetitions and read the backend's repetition warning **before** launching,
   not after.
4. **Watch it, and go in.** As runs finish, open the sandbox (terminal or
   desktop) on any run and look at what actually happened. Record your judgement
   with the manual score if you want it on the record — it is the same
   `POST /api/runs/{id}/score` used everywhere else.
5. **Read the result.** A pass rate **per arm** — `N of M` — plus the per-case
   grid and the cost per arm. There is no pooled number and no control/treatment
   delta, because averaging the arms would describe a configuration nobody runs.

The only sentence this lane licenses is **"this subject passed N of M under
`<harness>`/`<model>`"**. It is not evidence about the harness: no arm was
ablated, so there is nothing to attribute a difference to. If that is the
question you actually have, use Lane A or B.

Two designs are refused outright: varying the **target repository** (the
sentence stops being readable — passed 8 of 9 in *which* repo? that is
`cross_repo`) and disabling skills (**ablation is Lane A's question**, and
allowing it here builds a harness evaluation wearing the wrong label).

## What to look at when judging a skill

- **Objective**: did the validation pass rate / composite change across reps?
- **Cost/effort**: tokens, steps, time — a skill that costs 30% more tokens for
  the same score is a redundancy signal.
- **Attribution**: was the skill even consulted? (Lower bound: only SKILL.md
  file reads are observable; context-injected usage is not.)
- **Audit verdicts**: the structured pass rates each item useful / redundant /
  improve with a cited note — read it as an informed opinion, not ground truth.

## Cautions

- Don't run `make sync-skills`, edit skills, or commit while a run is
  executing: the runner temporarily owns the generated skill views.
- If a run is SIGKILLed mid-ablation the tree stays ablated until the next run
  (restore-at-start) or a manual `make sync-skills`.
- `data/skills_staging/` is a materialization, not the source of truth. If it is
  wiped or edited behind the app, the **Health** screen reports the drift and
  rebuilds it from the DB ([ADR-0018](adr/0018-health-and-drift-panel.md)).
- Staged-skill arms recorded before the whole-tree digest landed (ADR-0017) have
  `condition_hash` values computed a different way. `metadata.json` records
  `skills_extra_hash_algo`; treat the two generations as separate populations.
- Compare like with like: same case, prompt variant, models, and max steps —
  vary ONE factor per experiment.

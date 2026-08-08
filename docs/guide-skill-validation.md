# Guide: validating a skill with the lab

How to answer "does this skill actually help?" with runs instead of opinions.
Two lanes share the same machinery (ADR-0011): **ablate** an existing governed
skill, or **trial** a brand-new staged skill.

## The core idea

A run's `condition_hash` identifies its experimental arm (harness + model +
prompt + case, ADR-0003). Disabling a skill — or adding a staged one — flips
the hash, so "with" and "without" runs are distinct arms you can compare on the
multidimensional score (ADR-0004), the attribution panel (ADR-0005), and the
LLM audit's per-item verdicts (útil / redundante / a mejorar).

Local models at temp>0 are statistical: run **N repetitions per arm** (3+ is a
reasonable start) and compare distributions, not single runs.

## Lane A — ablate an existing skill

1. Open **Runs → New run**, expand the **Skills** section.
2. Uncheck the skill(s) under test; everything else stays identical to your
   baseline configuration. Create the run.
3. Create the baseline run with all skills checked (same case, model, prompt,
   max steps).
4. During an ablated run the runner regenerates `.opencode/skills/` and the
   `OPENCODE.md` skills block without the skill, and restores them afterwards
   (`git status` must be clean once the run ends).
5. Compare the arms in run detail / Compare: objective validation, tokens,
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
3. In **Runs → New run → Skills → Experimental**, check the staged skill
   (off by default) and launch. Launch the baseline without it.
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

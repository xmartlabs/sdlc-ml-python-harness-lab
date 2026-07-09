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

1. Open **Skills → New staged skill**. Name it kebab-case, paste/write the
   SKILL.md (frontmatter `name:` + `description:`, then the workflow body).
   It is stored in the DB and materialized at `data/skills_staging/<name>/`
   (git-ignored) — the governed catalog is untouched.
2. In **Runs → New run → Skills → Experimental**, check the staged skill
   (off by default) and launch. Launch the baseline without it.
3. Iterate: edit the staged skill and re-run. Each content edit flips the
   `condition_hash` (the hash covers the SKILL.md content), so arms stay
   distinguishable.
4. When it earns its keep, press **Promote**: the file is written to
   `.github/skills-external/<name>/SKILL.md` and `make sync-skills` runs.
   **Nothing is committed** — review `git status` and commit yourself.
5. If it doesn't earn its keep: Archive (kept for the record) or Delete.

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
- Compare like with like: same case, prompt variant, models, and max steps —
  vary ONE factor per experiment.

# Skill improvement methodology (human-driven)

A light, repeatable loop you can exercise by hand. The lab only touches **two
edges**: it hands you a **sanitized issue**, and later it **reads the result** by
re-measuring. Everything in between is yours. The heavy tooling (immutable release
tags, sync worktrees, the regression suite, attestations) is **optional rigor** for
when you want full traceability — it is *not* the methodology.

## The loop

```
1. Evidence      (lab)   run a case → audit → spot a weak skill, with a MEASURABLE symptom
2. Issue         (lab → ml-python-base)   generate a sanitized issue; you paste it into GitHub
3. Iterate       (you, on ml-python-base) improve the skill freely with your own agent/LLM; merge
4. Re-measure    (lab)   re-run the SAME case → did the symptom improve?
```

### 1. Evidence

Run a case (a real SWE-bench case gives the richest signal) and audit it:

- In the UI: create a run (harness `claude`, prompt variant `swe_harness` / `sdlc` so
  governance + skills are actually loaded), let it finish, open the run.
- Look at the **Attribution** panel (skills consulted) and run the **Audit** (button →
  `audit.md`). The audit's "skills / loops / SDLC adherence" sections tell you which
  skill underperformed and *how*.

The output you want is a weakness with a **measurable** symptom, e.g. *"systematic
debugging churned hypotheses and re-read the same file every step"* →
`repeated_equivalent_command_max` should be `<= 2`.

### 2. Issue (the only thing that crosses into the template)

Generate a **sanitized** issue for `ml-python-base` and open it there:

- **UI (recommended):** on the run, click **"Generar issue sanitizado"** → copy the
  whole markdown block → open a new issue at `marcosdh1987/ml-python-base`. The first
  line (the `# …` heading) is the issue title; the rest is the body. No `gh`, no
  private data (run IDs, local paths, and secrets are stripped; only aggregate
  evidence and the target skill/paths are surfaced).
- **CLI (equivalent):** `make harness-proposal RUN=<id>` writes a tracked
  `data/harness-proposals/<year>/HEP-….yaml` and prints the same sanitized issue.
- **Advanced / optional:** `make harness-proposal-issue PROPOSAL=<id> CREATE=1` files
  it via `gh`. Skip it if you'd rather copy-paste.

A good issue names: the **skill**, the **behavior that failed**, the **suggested
change**, and a **success criterion** (`metric operator value`) you can re-measure.

### 3. Iterate (yours, outside the lab)

On `ml-python-base`, improve the skill however you like — your own agent, your own
edits — referencing the issue. Edit the governed source under `.github/skills/…`,
verify (`make check` / `make check-sync`), and merge. **The lab does not participate
here.** This is the part you own and exercise.

### 4. Re-measure (how the lab "reads" your improvement)

When you're done, bring the improved skill into the lab's governance and **re-run the
same case**, then compare — by hand, in the UI:

1. Update the lab's governed skill to the new version. Lightest: `make template-sync
   REF=<branch-or-tag>` (or edit the skill and `make sync-skills`). Rigorous: cut a
   release and use `make harness-sync-branch REF=vX.Y.Z` (isolated worktree).
2. Re-run the **same case** (same harness/model/prompt) — in the UI, "Run again".
3. Compare in the run's **Attribution** + **Audit**: did the symptom move? Did the
   success criterion hold (`repeated_equivalent_command_max <= 2`, still resolved)?

That comparison **is** the lab reading your improvement. Eyeballing two runs is enough
for the exploratory loop.

**Closing the loop — the objective signal.** One re-run tells you the symptom moved;
re-running the same case across your whole **model tier** (e.g. Haiku, Sonnet, Opus)
tells you the fix *generalized* rather than just fitting one model. The objective
close signal is that re-run producing **zero proposals**: the deterministic outcome
gate (see `improvement-cycle.md` — objective passed, score ≥ 8, 0 interventions, 0
audit items judged `improve`) skips synthesis for a clean run, so `make improve`
returning `proposals: []` on every model means the cycle is closed for that case. A
proposal that reappears on only one model marks a gap that fit the others but not that
one — worth another iteration, not a closed loop.

## When you want rigor (optional)

The same evidence → issue → change → re-measure loop has a fully-traceable form:
publish an immutable `vX.Y.Z` release of the template, prepare an isolated candidate
worktree (`make harness-sync-branch`), and run the **canonical regression suite**
(baseline vs candidate, hard gates + warnings + a private validation report). Use it
when you want to *adopt and publish* an improvement with provenance — not for every
exploratory iteration. See `lab-sync-workflow.md` and the regression suite in
`benchmarks/harness-regression-v1/`.

## Observability caveat

Attribution counts a skill as "consulted" only when its `SKILL.md` shows up in the
read log (skills are context-injected, so this is a lower bound). The **LLM audit**
reads actual behavior and is the stronger signal for deciding what to improve — lean
on it, not on the raw "consulted" count.

# Improve the template

`ml-python-base` is the shared harness every team starts from. The lab exists partly
to make it better on evidence: a symptom observed in runs becomes an issue, the
issue becomes a change to a skill or a rule, and the change is measured before it
is trusted.

```
run or experiment ──► audit ──► proposal ──► issue in ml-python-base
        ▲                                              │
        └──── re-measure ◄── adopt the release ◄── fix & release
```

## 1. Observe a symptom

Two sources, both on a run page:

- **Run audit** — a model reads the run's artifacts and reports how the agent worked:
  skills consulted or ignored, loops, steps skipped in the working loop.
- **Propose improvements** — turns the audit into concrete proposals, each naming
  the file to change (a skill, a rule, an adapter). A clean run proposes nothing;
  that is deliberate.

**Save** the proposals you agree with. One run is an anecdote; save proposals from
several runs that show the same thing.

## 2. File one issue

**Advanced → Improvements** collects the saved proposals across runs. **Generate
combined issue** writes one sanitized issue — no paths, keys or private data — with
the evidence and the files it targets. Copy it into a new issue on
[`ml-python-base`](https://github.com/xmartlabs/sdlc-ml-python-base); the first line
is the title.

From the shell, `make improve RUN=<run-id>` drafts the issues for one run, and
`CREATE=1` files them with `gh`.

When you have an **experiment** rather than a run — "the harness prompt does not
help on bugfix cases" — record it as a **finding** (**Evidence → Findings**), with
the experiment as its evidence, and write the issue from the finding. Proposals are
generated per run; a finding is the cross-run claim, written by a person.

## 3. Fix and release (in `ml-python-base`)

Change the skill or the rule, run that repository's gate, merge, and tag a release
(`vX.Y.Z`). This step is a normal pull request — the lab does not edit the template.

## 4. Adopt the release in the lab and re-measure

```bash
make harness-status                   # which template version the lab runs
make harness-sync-preview REF=vX.Y.Z  # what would change, read-only
make harness-sync-branch REF=vX.Y.Z   # prepare it in an isolated worktree
make harness-adopt REF=vX.Y.Z         # once merged: runs now record template_ref=vX.Y.Z
```

Then measure the change the way you would any other:

- **One symptom** — re-run the case that showed it (same harness, model and prompt)
  and compare its audit with the earlier run.
- **A release** — **Evaluate → New evaluation**, tick **Use the regression suite**,
  previous version as control, new one as treatment. The answer arrives as cases
  *fixed*, *broken* and *unchanged*.

A release that fixes the symptom and breaks nothing is validated. One that breaks a
case is not, however good the fix looks.

## Reference

- When to trust a proposal, and when not: [improvement methodology](harness-lifecycle/improvement-methodology.md)
- Adopting a release step by step: [lab sync workflow](harness-lifecycle/lab-sync-workflow.md)
- The audit → proposal → issue cycle in detail: [improvement cycle](improvement-cycle.md)
- The rigorous form — immutable releases and a hard-gated regression suite:
  [harness lifecycle](harness-lifecycle/README.md)

Back to [start here](README.md).

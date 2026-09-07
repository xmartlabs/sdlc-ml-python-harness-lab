# Harness improvement lifecycle

How the lab turns what it observes about an agentic harness into concrete
improvements to the shared template (`ml-python-base`), and back.

## Start here

**[improvement-methodology.md](improvement-methodology.md)** — the human-driven
playbook. A light 4-step loop you can exercise by hand:

```
1. Evidence   (lab)   run a case → audit → spot a weak skill, with a measurable symptom
2. Issue      (lab → ml-python-base)   generate a sanitized issue; paste it into GitHub
3. Iterate    (you)   improve the skill with your own agent/LLM; merge
4. Re-measure (lab)   re-run the same case → did the symptom improve?
```

Everything else on this page is **optional rigor** for when you want full
traceability — it is not required to run the loop.

## Reference

| Doc | What it covers |
|---|---|
| [improvement-methodology.md](improvement-methodology.md) | The everyday loop (start here). |
| [proposal-workflow.md](proposal-workflow.md) | Turning run evidence into a durable, sanitized HEP proposal + issue. |
| [lab-sync-workflow.md](lab-sync-workflow.md) | Consuming an immutable template release: installed state, preview, isolated candidate worktree. |

The full initiative (release contract, regression suite, adoption/rollback) lives in
[docs/plans/2026-07-harness-continuous-improvement-cycle.md](../plans/2026-07-harness-continuous-improvement-cycle.md).

## The two edges the lab touches

- **Out:** a sanitized improvement issue for `ml-python-base` — from the run's
  **"Generate sanitised issue"** button (the *Sanitised issue* panel) in the web UI, or `make harness-proposal RUN=<id>`.
- **In:** re-measuring the same case after you've improved the skill — by hand in the
  UI, or rigorously via the regression suite (`benchmarks/harness-regression-v1/`).

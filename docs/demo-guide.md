# Demo guide — the lab in 20 minutes

A script for showing the lab to a development team, and the checklist each person
follows afterwards to try it on their own project. Everything below works with a Claude
subscription alone; a gateway adds self-hosted models and per-run cost tracking.

## The story (2 minutes)

Some teams already move much faster with AI than others, and the model is rarely the
reason. The difference is the **harness**: what the agent reads before it starts, which
rules it cannot skip, and how it proves its own work. A harness is code in the
repository — `AGENTS.md`/`CLAUDE.md`, skills, hooks, the verify target, CI — and like
any code it can be wrong in ways nobody notices:

- a `make test` that prints "✅ Tests completed!" when tests fail, so every "tests pass"
  an agent reports is unverified;
- a "verify before you finish" hook that only prints a reminder the model never sees;
- a 200-line rules file every session pays for that, per 2026 research, rarely raises
  success at all.

The lab answers three questions about a harness with evidence instead of opinion:
**is it sound, does a change to it help, and does it still help with a stronger model.**
It measures the harness and the workflow. It never scores or ranks people.

## Before the session (15 minutes, once)

```bash
make app-up              # API :8009, UI :5173 — benchmark cases are seeded at startup
make demo-check          # docker, credential (names only), API, cases — writes nothing
```

Then run one experiment ahead of time so there is a finished result to show if a live
run is slow: **Home → Evaluate a harness change**, case `cli-textstats-v1`, two arms
(no harness vs. the lab harness), 3 repetitions, Haiku. About 15 minutes and under $3.

## Live, step by step (16 minutes)

### 1. Check a real repository's harness — 3 minutes, no cost

In a terminal, on a repository the audience knows:

```bash
make harness-check REPO=../their-project
```

Eight checks decided by code, not by a model, each with its fix. Run it on
`ml-python-base` too: it finds a real failure (`make test` swallows failing tests). The
point to make: these are defects in *infrastructure the agent trusts*, found in seconds.

### 2. Audit a repository in the UI — 4 minutes

**Home → Audit a repository.** Point it at a local clone, pin the commit, run the audit.
Show, in order:

- the **Checks** count in the fact row and the *Chequeos determinísticos* section —
  the same eight checks, now inside a report;
- the component map — what the model read and what each component claims to do;
- the proposed cases — which of them the runner can actually execute.

If the audit is slow, open a finished one under **Evidence → Repositories**.

### 3. Does a harness change help? — 5 minutes

Open the experiment you ran before the session. Read it the way the lab wants it read:

- **one rate per arm with its interval** — "60% [23–88]" at 5 runs is a wide range,
  and the page says so instead of calling it proof;
- **the case matrix** — which cases the harness fixed, broke or left alone; an average
  can hide "fixed two, broke one";
- **cost and tokens per arm** — a harness that costs 40% more for the same outcome is a
  result too;
- **validity** — if the control already passes everything, the lab says *no headroom*
  rather than "no effect".

### 4. Validate your own tool or skill — 4 minutes

**Home → Validate your tool.** Pick a skill or MCP server someone on the team built,
describe what success looks like, and launch. This is the mode that needs nothing set up
first, and it leaves behind a case the team can rerun after every change.

### Optional — frontend and stronger models (if there is time)

- **Frontend, graded in a browser.** `todo-web-v1` grades an accessible todo app in
  headless Chromium: keyboard flow, `aria-pressed`, persistence and an injection
  attempt. A plausible build that "looks done" scores 9 of 12 — the three it misses are
  exactly the ones a review usually misses.
- **Does a stronger model need less harness?** **Home → Does a stronger model need less
  harness?** builds the full grid (no harness vs. the lab harness × Haiku, Sonnet, Opus)
  and reports the harness effect per model, the change from smallest to largest, and the
  cost per resolved task in each cell. Use three cases or more: below that the change is
  never called established.

## What the lab will not tell you

- **Who is good at AI.** Nothing here scores a person; results are per harness, model
  and case. Team-level delivery metrics live in the gateway's Observatory, also with no
  per-person view.
- **A settled verdict from three runs.** Two to four repetitions per arm is a
  judgement, five or more a validation — and either way the interval shows how wide
  the result still is.
- **That a harness is good in general.** A result holds for the cases, model and
  harness version it ran on — which is why every run records all three.

## Try it on your own project (checklist)

1. `make harness-check REPO=<your repo>` — fix the failures first; they are usually a
   one-line change (a swallowed exit code, a non-blocking hook).
2. Audit it in the UI and read the proposed cases.
3. Pick one task your team does often, turn it into a case with a test that fails
   before the change and passes after.
4. Run it with and without your harness, 5 repetitions each, on the model you use.
5. Keep the harness pieces that move the result; delete the ones that only cost tokens.

## If something goes wrong

| Symptom | Likely cause | Fix |
|---|---|---|
| `make demo-check` says no credential | `.env` has neither gateway nor Claude credential | [Onboarding](onboarding.md) §credentials |
| A run sits in `pending` | worker or Redis is down | `make app-up`, then Health |
| An audit fails immediately | the repository is not pinned | pin a commit on the readiness step |
| Results show `no_headroom` | the case is too easy for the model | use a harder case or a smaller model |

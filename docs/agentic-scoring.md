# Agentic scoring: how this harness maps to assessments of AI-assisted coding

This document explains how the working loop in `docs/agentic-workflow.md` relates to
the kind of assessment that gauges *how well you use AI for coding*, and — honestly —
what a template repository can and cannot influence. It refers to such assessments
generically; the principles hold regardless of which specific tool you use to measure.

## What such an assessment actually measures

Measurement resources in this space typically do **not** score your repository's
static files. They read your session transcripts (for Claude Code, these live under
`~/.claude/projects/`) and score your **behavior across all sessions and projects**.
Common outputs are some blend of:

- A **build-quality scorecard** — along axes such as Execution, Planning, and
  Engineering.
- An **overall agentic-skill score** — usually a weighted composite across a few
  dimensions like *breadth*, *craft*, *efficiency*, and *savvy*.

The key consequence: **a template cannot raise your score by existing.** For example,
a "compounding" signal typically counts when you *write to* places like `memory/`,
`docs/adr/`, `CLAUDE.md`, or `AGENTS.md` during a session — not when those files
merely exist. The template's job is therefore to **make the rewarded behaviors the
natural default**, not to game a checklist.

The good news: every behavior these assessments reward is genuinely good practice.
Optimizing for them and optimizing for real engineering quality are the same thing.

## The principle behind the signals (so you can reason about any rubric)

You do not need to memorize metric names. Every signal in a serious agentic
assessment is a proxy for one underlying question: **"Is this person using the agent
as a force multiplier, or as a faster way to make the same old mistakes?"** The
common dimensions are just that question asked from a few angles:

- **Breadth** — *Do you command the whole toolbox?* Many skills, many tools (MCP +
  CLI), real orchestration of subagents. Proxy for: you reach for the right
  instrument instead of hand-coding everything serially.
- **Craft** — *Is the output trustworthy and durable?* Verification (you test), and
  compounding (you write down what you learn). Proxy for: your work holds up and makes
  the *next* task easier. Durability tends to carry the most weight — it compounds.
- **Efficiency** — *Do you steer well and recover from errors?* Proxy for: you give
  the agent enough to act but not so much that you're micromanaging, and you bounce
  back from failures instead of getting stuck.
- **Savvy** — *Are your habits lean?* Matching model to task, CLI over heavier tools,
  loading capabilities on demand. Proxy for: you don't burn budget out of habit.

A useful mental test for any practice: *"If I did more of this, would a senior
engineer who pairs with AI all day nod or wince?"* If they'd nod, it moves the score
for the right reasons. If they'd wince (an ADR for a one-line fix, a test that asserts
nothing), it's noise — and good assessments are designed to not reward noise.

## How the loop maps to the rubric

| Loop step | Harness support | Behavior assessments tend to reward |
|-----------|-----------------|-------------------------------------|
| **Ground** | `/plan`, Explore subagents, `research_current_info` | Grounding — exploring before producing (a high explore-to-doing ratio). |
| **Plan** | `/plan`, `TodoWrite`, `writing-plans`, `brainstorming` | Planning ceremony + discipline (plan-driven work). |
| **Delegate** | `/orchestrate`, `.github/agents/`, `subagent-driven-development` | Orchestration — real parallel fan-out, not serial grinding. |
| **Verify** | `/verify`, `make check`/`test`, `verify_changes`, `requesting-code-review`, `systematic_debugging` | Verification — frequent test runs and review; low rework and error rate. |
| **Compound** | `/adr`, `/retro`, `retrospective`, `memory/`, `docs/adr/` | Compounding — writing durable knowledge (usually the heaviest craft signal). |
| **Across all** | CLI-first (`make`/`uv`), ToolSearch, MCP servers, model tiers | Tool command, token economy, model mix (breadth + savvy). |

## What this template can move — and what it can't

**Strongly influenced (the template makes these the default):**

- **Compounding** — `memory/` and `docs/adr/` conventions + `/adr` and `/retro` make
  writing durable knowledge routine.
- **Verification** — `/verify`, the `make` gates, and the verify nudge make test runs
  and review part of every change.
- **Orchestration** — `/orchestrate` and the governed agents make parallel delegation
  easy when work is independent.
- **Planning / Grounding** — `/plan` and the playbook make explore-then-plan the norm.

**Only nudged (these are personal habits the template can suggest, not enforce):**

- **Model mix / token economy** — using more than one model and offloading routine
  sub-work to cheaper models; CLI-first over MCP. The playbook recommends it; you do it.
- **Raw volume / velocity** — driven by how much you actually build.

**Honest caveats:**

- Most assessments aggregate *all* your sessions, so changes here move your score
  **gradually and globally**, not instantly.
- Don't perform ceremonies for the metric's sake. An ADR for a trivial change or a
  test run that verifies nothing is noise. Do the real thing; the signal follows.

## Measuring yourself

If you use a measurement tool, prefer a local-first one and understand its data flow
before running it (nothing should leave your machine unless you explicitly opt in).
Work through a few real tasks using this loop, then compare your scores before and
after. Expect the clearest movement in the durable-knowledge, test-run, review, and
parallel-delegation signals — exactly the behaviors this harness is built to support.

See `docs/agentic-workflow.md` for the loop itself.

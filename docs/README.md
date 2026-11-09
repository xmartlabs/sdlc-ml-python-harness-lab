# Harness Lab — start here

**The lab answers one question about how you build software with AI: does this
harness, this skill or this model actually make the agent do the job — and which
one, for which kind of task?** You bring a task, a repository or a skill; the lab
runs coding agents (Claude Code, OpenCode, Codex) on it in isolated containers,
under the configurations you choose, scores every run against checks the agent
did not write, and tells you how sure it is.

What a developer takes away:

- **A measured answer instead of a vibe.** "Opus with the full harness passes
  12/12; Haiku passes 10/12 with or without it" — with an interval, not an
  anecdote.
- **A verdict on your own repository's harness**: what is missing, what is
  broken, what to fix, from checks that read the files, not a model's opinion.
- **The pieces that work, to keep.** The governance, the agents, or one skill,
  copied into your project with `make harness-export` — previewed first.
- **Evidence that improves the shared template** (`ml-python-base`) for the next
  developer.

## Six journeys

Each is one page. Read the one you came for.

| You want to… | Read |
|---|---|
| Turn an idea, a benchmark issue or a repo task into a **test case** | [Create a case](journey-create-a-case.md) |
| **Run it** with different models, harnesses, skills — repeat it, continue it | [Run and compare](journey-run-and-compare.md) |
| Know **what a result says**, and when to believe it | [Read the results](journey-read-results.md) |
| **Audit your repository** or **measure your skill** | [Bring your repo or skill](journey-bring-your-repo-or-skill.md) |
| **Take the harness, or one skill,** into your project | [Take it home](journey-take-it-home.md) |
| Turn what you learned into a **better template** | [Improve the template](journey-improve-the-template.md) |

First time on this machine? Set it up with the [onboarding guide](onboarding.md)
(about 15 minutes), then come back here. Showing it to a team? The
[demo guide](demo-guide.md) is a 20-minute script.

## The ten-minute tour

```bash
make harness-check REPO=../your-project   # 1. your repo's harness, no stack, no model
make app-up                               # 2. the lab, at http://localhost:5173
```

3. **Evaluate → Harness x model** → pick `clinic-slots-t1`, `clinic-booking-t2`,
   `clinic-rules-t3`, two models, 2 repetitions → **Create and launch**.
4. Open the experiment when it finishes and read it top to bottom
   ([what each panel means](journey-read-results.md)).
5. Found a skill you like? `make harness-export REPO=../your-project SKILLS=<name> GOVERNANCE=0`.

## Reference

Kept for when a journey sends you there.

| Topic | Page |
|---|---|
| Every screen of the web UI | [Web UI guide](guide-web-ui.md) |
| Writing a case that measures something | [Case formation](guide-case-formation.md) |
| claude / opencode / codex: what each needs | [Harness adapters](harness-adapters.md), [claude & codex runbook](runbook-claude-codex.md) |
| Small and self-hosted models | [Tune your local model](guide-local-models.md), [Small models](guide-small-models.md), [Local model runtime config](local-model-runtime-config.md), [Task sizing](task-sizing.md) |
| Registered repositories | [Target repositories](guide-target-repositories.md), [What the lab can run](guide-runner-capabilities.md) |
| Skills | [Skill validation](guide-skill-validation.md), [Skills management](skills-management.md) |
| Continuing a run | [Run continuation](guide-run-continuation.md) |
| SWE-bench | [Curated set](swebench-curated-set.md) |
| Run traces | [Langfuse](guide-langfuse-observability.md) |
| What the lab does not do | [Known limitations](known-limitations.md), [SECURITY.md](../SECURITY.md) |
| Why it is built this way | [Architecture decisions](adr/README.md) |
| Working on the lab itself | [Agentic workflow](agentic-workflow.md) |

## History and design notes

Not guides: how parts of the lab were designed, kept for the reasoning. Nothing
here is needed to use it.
[Lab v2 roadmap](roadmap-lab-v2.md) ·
[Local web app roadmap](roadmap-local-webapp.md) ·
[SWE-bench cases](design-swe-bench-cases.md) ·
[SWE-bench Pro lane](design-swe-bench-pro-lane.md) ·
[Harness architecture](harness-architecture.md) ·
[Legacy CLI runbook](runbook-first-run.md) ·
[Gateway changes](GATEWAY_CHANGES.md) ·
[Tetris walkthrough](vibe-coding-tetris-example.md) ·
[Agentic scoring](agentic-scoring.md) ·
[Harness lifecycle](harness-lifecycle/README.md) ·
[Plans](plans/)

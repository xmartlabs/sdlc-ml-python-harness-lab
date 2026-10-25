# Run and compare

One run tells you what happened once. Agents are stochastic, so what you usually
want is a **comparison**: the same cases, run several times, under configurations
that differ in exactly one thing. The lab builds that design for you and refuses
one it cannot answer.

## What can vary

| Dimension | Values | Where it is chosen |
|---|---|---|
| **Harness CLI** — which agent drives the run | `claude` (Claude Code), `opencode`, `codex` | every run form |
| **Model** | the Claude family, anything your LiteLLM gateway serves, LM Studio / Ollama directly | every run form |
| **Governance** — which rules, agents and skills the agent sees | `bare` (none), the lab's (`lab_root`), the target repository's own (`native`), or the lab's copied into the target (`injected`) | New evaluation, Harness x model |
| **Skills** | add one, remove some, or force the agent to invoke them | New evaluation, Skills |
| **Prompt** | `default` (the task) or `harness` (the task plus "follow the repository's working loop") | New evaluation |

## Three ways to run

### A single run — "does this work at all?"

**Advanced → Runs → New run.** Pick a case, a harness, a model, a prompt variant;
watch the log stream. Good for trying a new case or a new model once. It proves
nothing about *better*.

### An experiment — "does X help?"

**Evaluate → New evaluation.** State the question, pick the cases, pick **one
factor** to vary:

| Factor | Compares |
|---|---|
| **Full harness** | no harness at all vs governance + skills + harness prompt |
| **Governance body** | no harness vs the rules alone, skills hidden |
| **Add skills** | the harness vs the harness plus skills you staged |
| **Remove skills** | the harness vs the harness without the skills you pick |
| **Harness prompt** | the plain task vs the task plus the harness preamble |
| **Activation** | skills available vs skills explicitly invoked |
| **Governance source** | a repository's own harness vs the lab's injected into it |
| **Swap the harness** | one CLI vs another (claude vs opencode) on the same model |

The model is held fixed across the arms, so the difference is the factor's.
Choose the repetitions: **1** is exploratory (no headline), **2** a judgement,
**5** a validation. Cases matter more than repetitions — three cases at two
repetitions say more than one case at six.

### Harness x model — "which model needs how much harness?"

**Evaluate → Harness x model.** Every harness level on every model, as one grid:
no harness → governance only → full harness, on two to four models. It answers
the question the others cannot — whether the harness helps a small model more than
a large one, and which tier of task each model does reliably. Five repetitions on
the clinic ladder (`clinic-slots-t1`, `clinic-booking-t2`, `clinic-rules-t3`) is the
calibrated starting point ([small models](guide-small-models.md)).

From the shell, `make ladder MODEL=<id> LAUNCH=1` queues the same harness ladder for
one model.

## Choosing the harness CLI and the model

- **claude** runs in the Docker runner with `CLAUDE_CODE_OAUTH_TOKEN` (your Claude
  plan) or `ANTHROPIC_API_KEY` in `.env`. In Docker it reaches Anthropic through
  your gateway, so the gateway needs a card for the model; the form marks the
  models it does not serve, and launching one is refused.
- **opencode** runs in Docker against any `gateway/…` model, or directly against
  LM Studio / Ollama on your network — this is the lane for self-hosted models.
- **codex** runs on the host only: `make app-host`.

Details and troubleshooting: [harness adapters](harness-adapters.md). Before
measuring a small self-hosted model, check its context window on **Settings →
Check context** — below 32k tokens it cannot hold an agent's system prompt.

## When a run does not get there

**Continue it.** On a finished, failed or cancelled run, the **Continue** panel
starts a child run in the same workspace with a new prompt — "the tests still
fail on Windows paths, fix that". The child is `<run>-C-001`, keeps the parent's
configuration and is linked to it. A continuation is not added to the parent's
experiment: it measures a person steering, not the configuration
([run continuation](guide-run-continuation.md)).

**Run it again.** **Run again** re-queues the same configuration as a fresh run,
inside the same experiment — that is how you add repetitions after the fact.

## Bringing a different repository

Everything above runs in the lab's own workspace. To run the same comparison inside
*your* repository, register it first
([bring your repo or skill](journey-bring-your-repo-or-skill.md)); the forms then
offer it as the venue.

**Next:** [read the results](journey-read-results.md).

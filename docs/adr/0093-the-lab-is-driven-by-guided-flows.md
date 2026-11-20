# ADR-0093: The lab is driven by guided flows with a live diagram

- **Status:** Accepted
- **Date:** 2026-11-19
- **Deciders:** Marcos Soto
- **Related:** [ADR-0090](0090-the-navigation-is-grouped-by-capability.md),
  [ADR-0092](0092-a-skill-is-trialled-on-a-chosen-base-and-improved-in-a-loop.md)

## Context

ADR-0090 grouped twenty screens into capability hubs. In use, three problems showed up.

- **Hubs pointed at forms, and the forms were still long.** The way a capability was
  used did not show.
- **A repetitions field turned "2" into 20 runs.** It clamped the value on every
  keystroke: "2" typed after a default became "12", then 10 per arm, across two arms.
  Nothing showed the total before creating.
- **Runs and Compare were harder to reach.**

The user asked for a few changes:

- choose with buttons, one question at a time;
- see a flow diagram that lights up what is chosen;
- keep long text in tooltips;
- keep runs, metrics and run comparison one click away.

A prototype went through four iterations before this was implemented.

## Decision

**Six guided flows**, at `/flows/<name>`:

| Flow | Varies | Creates (and, since 2026-11-20, can launch) |
|---|---|---|
| Models | the model | the Compare-models design (`subject_validation`, family `models-<case>`) |
| Harness | none → minimal → full, on one model | a ladder: one `harness_evaluation` A/B per adjacent pair of the chosen levels, in one family (`POST /api/experiments/harness-levels`); every case, 3D included |
| Skills | one skill, on a chosen base | `skill-trial` with `base` (ADR-0092) |
| Improve a skill | v1 → audited v2 | skill audit, staged draft, `skill-refine` (ADR-0092) |
| Metrics | nothing (reads) | — compare runs, what to improve, what each metric means |
| Your repo / Benchmarks | — | unchanged hubs |

Each flow declares only its steps. The shared `GuideFlow` component handles the rest:

- one question per step, answered with buttons, with long text behind an ⓘ;
- a trail of answers, where each answer is a way back;
- a flow diagram beside the step that lights up what is chosen and marks the next step,
  and whose nodes can be clicked to go back;
- a review screen with arms, runs, estimated cost and time before **Create (N runs)**.
  The estimate is the lab's own median for that case and model. With no measured run,
  it says so instead of guessing.

**One model, several harness levels, is a ladder, not one experiment.** Holding none,
minimal and full in one experiment would move two harness factors at once: none →
minimal changes governance *and* skills, which the validator flags. So the Harness flow
makes one clean A/B per adjacent pair of the chosen levels, in one family. This is the
harness ladder of ADR-0051, applied to the profile levels. With three levels, minimal
runs in both rungs, and the review screen counts it twice. Harness × model, which needs
two or more models, keeps its own screen.

**Repetitions are a stepper, 1 to 10.** Free text is gone, so "2" cannot become 20. The
older forms keep a text field, but it now parses the value on submit and shows the total
on the button.

**Runs are one click away from every page.** A pill at the top shows how many runs are
active, and the sidebar has **Runs** and **Metrics** under *Lab*.

**Home leads to the flows.** The showcase's buttons open a flow with the case already
chosen, and the 3D slides add **And without harness?**. The seven doors are the flows
(five since 2026-11-20; see the last amendment).

**Metrics documents itself from the code.**

- The metric list is the score registry (`/api/scores/definitions`).
- The criteria are the constants in `statistics.py` and `pruning.py`
  (`/api/metrics/criteria`), each shown with the file to edit.
- The text cannot drift from what the lab computes.

## Consequences

- A newcomer picks a flow by what they want to vary and is guided to a correct design.
  The design rules are enforced the same way as before, because each flow builds the
  payload an existing screen already built.
- Every existing route still answers. The hubs (ADR-0090) remain, and the menu points at
  the flows.
- The guide's estimate is a median. A first run on a new case and model shows "—".
- Adding a flow means one file in `pages/flows/` plus one entry in `FlowPage`.

## Amendment (2026-11-20): pick the agent, then its model; launch from the review

In use, the model step was a wall of cards. It mixed some sixty gateway cards, local
hosts and CLI models, and paired each model with a harness the person never saw.

**Each arm is picked in two clicks:** the agent CLI, then one of its models. The shared
`HarnessModelChooser` does this in every flow.

- **One tab per agent:** `opencode`, `claude`, `codex`, `gemini`. Each tab shows whether
  the agent can run now: ready, gateway, sign in first, or unavailable. The status comes
  from `/api/harness/clis`, the same source as System → Agent CLIs.
- **Under the tab, only that agent's models:**
  - opencode lists local hosts or gateway cards, then a chip per vendor, as the run
    form's model picker does;
  - claude, codex and gemini list their own CLI's models (`/api/models/native`).
- **A model the run would be refused for is shown, disabled, with the reason.** That
  covers a Claude model the gateway has no card for, an id opencode cannot route, and a
  CLI that is not signed in. It is the same rule the server's preflight applies.
- **The answer stores both:** `harness|model`. `armFor` reads it back. A bare model id
  from an older link still gets the old pairing.

**Models is case → Model 1 → Model 2 → repetitions.** Each model is its own step, so
the diagram lights each one separately.

- **Model 2 is optional.** "No, just this model" makes a one-arm probe in the same
  `models-<case>` family. "How does this new model do here?" is a common question, and a
  probe costs half of a pair. `subject_validation` has always accepted a single arm,
  since it reports per arm and never a delta. The probe runs one lane: two at a time is
  offered only with two arms.
- **The same model through two agents** (opencode vs claude on Opus) is a valid pair,
  labelled by agent.
- **Three or four models** stay on Compare models.

**Models asks which harness level every arm runs at.** It offers full, minimal or none;
the same level goes to every arm and is recorded in the experiment's notes.

- **Full is the default** and adds nothing to the arms. Every earlier model comparison
  ran at full, so new results line up with old ones, and two lanes stay possible.
- **Minimal or none** pin the same fields as a Harness-flow rung
  (`harness_source`, `skills_disabled`). Two at a time is not offered with them: the
  server refuses an ablation in parallel, because it rewrites the shared skill tree.
- **The estimate is per level.** `GET /api/estimate` takes `level=bare|minimal|full`, so
  a first bare run of a 3D case shows "—" instead of the price of full runs.

**The agent picker has a reasoning-effort row on the claude tab** (ADR-0081). It reads
*Default · low · medium · high · xhigh · max*, defaults to Default, and is chosen before
the model, so one click picks both.

- The answer becomes `claude|<model>|<effort>`.
- **In Models,** effort is pinned per arm as `executor_effort`, so Opus at high vs Opus
  at low is a valid pair.
- **In Harness, Skills and Improve,** the create endpoints take an optional `effort` and
  pin it in every arm, as they pin the model. Empty builds exactly what they built before.
- **Effort is never sent in a launch's shared config.** An opencode arm would inherit a
  claude-only flag, and the preflight would refuse it.
- **A model with no effort control** (Haiku 4.5) runs at Default and says so.

**The review offers "Create and launch" first, and "Create only" second.** Launch sends
the settings every arm shares: the case's own steps, its `default` prompt, and manual
score mode. It goes through the server's preflight, as the Experiments page does. Each
arm's pinned harness and model win over the shared ones. Models can run two at a time
when a worker serves the second lane. A refused launch leaves the experiment created,
says why, and links to it. This replaces "never launches" in the table above.

## Amendment (2026-11-20, later): five doors, not seven

In use, two pairs of doors asked one kind of question each.

- **Models and Harness.** Models already picked a harness level for every arm, so the
  Harness card read as a duplicate.
- **Skills and Improve a skill.** These are one loop: try, read why it failed, draft a
  v2, try v1 vs v2.

**Each pair is now one flow with two branches.**

- **Models and harness** (`/flows/models`) first asks *What do you compare?*:
  - *Models*: one or two models at one harness level, a `subject_validation` per arm;
  - *Harness levels*: one model, none → minimal → full, the ladder of
    `harness_evaluation` A/Bs.
- **Skills** (`/flows/skills`) puts *Try a skill* and *Improve a skill* side by side at
  the top. The choice is in `?do=try|improve`.

**The ladder is kept, not dropped.** Running each level as its own Models experiment
gives numbers with no comparison between them. Only the ladder's A/Bs carry a control,
a delta and a verdict per step up.

**The old routes redirect.** `/flows/harness` goes to `/flows/models?vary=levels` and
`/flows/improve` to `/flows/skills?do=improve`, keeping the rest of the query, so the
showcase's **And without harness?** and the links from Metrics land on the right branch.
Home has five doors: Models and harness, Skills, Metrics, Benchmarks and Your repo. The
sidebar's *Try* group follows.

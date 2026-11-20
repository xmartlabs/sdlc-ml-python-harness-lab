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

| Flow | Varies | Creates (never launches) |
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
chosen, and the 3D slides add **And without harness?**. The seven doors are the flows.

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

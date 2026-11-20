# ADR-0090: The navigation is grouped by capability, and Home shows real runs

- **Status:** Accepted
- **Date:** 2026-11-19
- **Deciders:** Marcos Soto
- **Related:** [ADR-0028](0028-evaluation-modes.md),
  [ADR-0070](0070-the-interface-speaks-english-and-spanish.md),
  [ADR-0078](0078-a-case-can-name-its-own-machine.md),
  [ADR-0089](0089-a-profile-grows-one-trialled-skill-at-a-time.md)

## Context

An onboarding review of the web UI (2026-11-19) measured what a newcomer meets:

- **20 screens in 6 groups** — Evaluate, Evidence, Local models, Advanced, System —
  named after the research method, not after what a person wants to try.
- **13,660 visible words**, with Skills at 8 screens tall and Cases at 6.
- Home explains five evaluation modes in about 760 words and never mentions local
  models, 3D games or SWE-bench.
- "Benchmarks" in the menu was a leaderboard. The case catalogue opened with import
  forms.
- Two fields in the Runs form were both called "Harness": the adapter and the
  skill profile.
- The skill experiments the lab is built around — additive trials, pruning,
  refinement — were terminal-only. The UI printed the `make` command as text.
- The welcome journeys were hidden as soon as one validated run existed, so the
  next person to join an established lab never saw them.
- The in-app Guide rendered ```` ```mermaid ```` blocks as plain code.

The lab had the capabilities. A newcomer could not see them, or see which ones were
built.

## Decision

**Group the navigation by capability: seven entries instead of twenty.**

| Entry | Hub route | Screens it reaches (routes unchanged) |
|---|---|---|
| Home | `/` | — |
| Models | `/models` | Compare models, Harness × model, Tune your local model, Local results |
| Harness & skills | `/harness` | New evaluation, Skills |
| Benchmarks | `/benchmarks` | Cases, grouped by family |
| Your repo | `/your-repo` | Audit, Validate your tool, Repositories |
| Results | `/results` | Experiments, Runs, Ranking (was "Benchmarks"), Findings, Compare, Improvements |
| System | `/system` | Settings, Health, Guide |

- **Each entry opens a hub.** A hub is a board of cards. Each card says what the
  capability does and where it lives. Its status chip reads **In the UI**,
  **Terminal only** or **To build**, so built and pending work are visible.
- **No screen moved.** Every existing route answers as before, and the legacy
  redirects stay. An entry stays highlighted while you are inside any screen it
  owns. The attention feed's routes are unchanged, and its contract test still
  holds.
- **Home leads with a showcase of real runs.** It rotates through four slides:
  - the racing3d car → kart continuation;
  - chess3d across models;
  - OpenCity, as the player sees it and as the grader measures it;
  - a replay of an agent-containment run.

  Every frame is a grader screenshot or a screen recording of that run. Every
  figure is the one the lab recorded. Each slide links to where you can try it.

  After the showcase come four doors (Models, Harness & skills, Benchmarks, Your
  repo), the lab's loop as a diagram, and four first journeys. The five evaluation
  modes stay one click away under "in detail".
- **The welcome journeys are always shown.** Someone joining an established lab is
  still new.
- **Case families are derived in the browser** (`frontend/src/caseFamilies.ts`).
  They come from the type, the tags and the machine profile the cases API already
  returns. No stored field is added. Families never feed a run, a condition hash or
  a score.
- **The additive skill trial gets a button.** The new endpoint
  `POST /api/experiments/skill-trial` builds the same design as
  `make add-skill-trial`, from a module both paths share
  (`harness_lab_app.skill_trial`). It **creates and never launches**. Launching
  stays on the Experiments page, behind the same launch form and preflight as every
  other experiment.
  - A second trial of the same skill gets a numbered id; the CLI keeps its
    deterministic one.
  - Both arms pin the chosen `executor_model` and `harness`. The Experiments
    launch form defaults to another model and harness, and an unpinned arm
    would run those instead. The values are the same in both arms, so the
    design still varies one factor. The CLI keeps passing them at launch.
- **Small fixes.**
  - The skill-profile field in Runs is labelled "Skills: minimal / full".
  - Skills is split into tabs: Catalog, Try, Import, Usage.
  - The Guide renders mermaid, and it is the only place that does.
    - The library loads lazily from a pinned CDN URL (`mermaid@11.17.2`, past
      the published XSS advisories) and falls back to the code block offline.
    - Every other `<Markdown>` shows agent-written text: artifacts, audits, run
      detail. Those keep mermaid as plain code, because injecting an SVG built
      from untrusted text is an XSS path.
- **The EN | ES switch stays where it is.** The user relies on it.

## Consequences

- A newcomer sees what the lab can do, and how, before reading any method.
- Hubs are static text. A new capability needs a card in its hub, or it stays
  invisible.
- Benchmarks and Harness & skills read live data: cases, profiles and the latest
  pruning verdict.
- The showcase is curated, not live. Its media lives in
  `frontend/public/showcase/` (about 1.1 MB) and its copy in
  `frontend/src/showcase.ts`. When a featured run is superseded, update both.
  - Every 3D run already writes grader screenshots to
    `data/runs/<id>/workspace/.lab-grade/shots/`. A future version can source
    slides from there automatically.
- No metric, score, run, hash or experiment design changed.
  - The new endpoint only adds a way to create an experiment the CLI could
    already create.
  - `make check` is green: 3,103 passed, 6 skipped.
  - `make check-frontend` is green.
- The Guide's mermaid rendering needs network access to jsDelivr the first time.
  Offline, diagrams stay as code, as before.

---
description: Plan a multi-step task before touching code (explore, then plan).
argument-hint: <what you want to build or change>
allowed-tools: Read, Grep, Glob, TodoWrite, Task
---

Produce a grounded plan for: **$ARGUMENTS**

Steps:

1. **Ground first.** Read the relevant code and skim `memory/context.md` and
   `memory/learnings.md`. Use Grep/Glob/Read (or an `Explore` subagent) to map the
   area before proposing anything.
2. Pick the right ideation skill: `brainstorm_quick` for a scoped change, or the
   external `brainstorming` skill when the work is design-impacting and deserves a
   written, approved spec. For execution planning use `writing-plans`.
3. Lay out the plan as an explicit `TodoWrite` list: phased steps, the files each step
   touches, and how each step will be verified.
4. Call out assumptions and the most decision-changing open questions — ask before
   committing to an approach.
5. Do **not** write production code in this command; output the plan and wait for go.

Respect governance: `.github/architecture.md`, `.github/standards.md`,
`.github/domain-boundaries.md`.

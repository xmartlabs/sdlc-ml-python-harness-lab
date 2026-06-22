---
name: brainstorm_quick
description: Use for fast ideation on a scoped feature when no written spec or formal approval is needed — diverge on options, weigh trade-offs, converge on a recommendation, then hand off to `plan_and_execute_feature`. For new features or design-impacting work that needs a written, user-approved spec, use the external `brainstorming` skill (full design gate) instead.
---

# Skill: brainstorm_quick

## Purpose

Explore a problem space quickly before any plan or code exists. A lightweight,
governance-aligned alternative to the heavier external `brainstorming` skill: no
mandatory design document, no hard gate — just diverge, weigh, and converge so the
`plan_and_execute_feature` skill can turn the chosen direction into an explicit plan.

## When to use which ideation skill

| Signal | Use `brainstorm_quick` (this skill) | Use external `brainstorming` |
|---|---|---|
| Scope | Scoped feature, bounded change, quick exploration | New feature or creative work with design impact |
| Artifact | None persisted — recommendation in conversation | Written design spec committed under `docs/` |
| Gate | No hard gate; user accepts the recommendation | Hard gate: user must approve the spec before any code |
| Handoff | `plan_and_execute_feature` | `writing-plans` (then plan execution) |
| Runtime needs | Any tool, text-only | Optional visual companion requires Node.js (text fallback exists) |

When in doubt, start here; escalate to the external `brainstorming` skill if the
discussion reveals design-impacting decisions that deserve a written, approved spec.

## Required Input

- Problem statement or feature idea (may be rough or ambiguous).
- Known constraints (deadlines, stack, non-functional requirements).
- Optional: prior attempts, hard requirements, things explicitly out of scope.

## Output Format

- Restated problem and the assumptions being made.
- Open questions for the user (ask the most decision-changing ones first).
- 2-4 distinct candidate approaches, each with trade-offs (effort, risk, fit).
- A recommended direction with rationale.
- A short handoff note to `plan_and_execute_feature`.

## Execution Rules

1. Diverge before converging: do not settle on one option until alternatives exist.
2. Ask clarifying questions when the input is ambiguous — do not invent requirements.
3. Challenge assumptions explicitly and call out unknowns and risks.
4. Stay within governance: `.github/architecture.md`, `.github/standards.md`,
   `.github/domain-boundaries.md`.
5. Do not write production code in this phase — output ideas, options, and a
   recommendation only.
6. Keep all artifacts in English.
7. When the target executor is a weak/self-hosted model, bias the recommendation
   toward the **simplest option that runs** (not the most elegant), and hand off a
   backlog sliced into one-file, runnable-first steps. See `docs/task-sizing.md`.

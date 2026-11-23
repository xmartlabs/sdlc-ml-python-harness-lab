---
name: retrospective
description: Use at the end of a unit of work to capture durable, non-obvious knowledge into project memory (memory/) and flag decisions worth an ADR. Turns one-off discoveries into compounding, persistent context.
---

# Skill: retrospective

## Purpose

Close out work by persisting what was learned so future sessions (human or agent) do
not re-derive it. Keeps `memory/` a living, trustworthy asset.

## Required Input

- The change or task just completed (diff, branch, or summary).
- Notable surprises, gotchas, decisions, or new conventions encountered.

## Output Format

- Concise entries appended to the correct memory file:
  - `memory/learnings.md` — facts and gotchas (dated entry format).
  - `memory/patterns.md` — conventions others should follow.
  - `memory/context.md` — updated active focus / open threads.
- A short summary of what was recorded.
- A note on whether any decision warrants an ADR (`docs/adr/`).

## Execution Rules

1. Record only durable, non-obvious knowledge. Skip anything already clear from the
   code, the README, or commit messages.
2. One fact per entry; keep entries short and self-contained.
3. Use absolute dates (`YYYY-MM-DD`), not relative ones.
4. Delete or correct memory entries that turned out to be wrong.
5. If a significant, hard-to-reverse decision was made, recommend writing an ADR.
6. Keep all artifacts in English.

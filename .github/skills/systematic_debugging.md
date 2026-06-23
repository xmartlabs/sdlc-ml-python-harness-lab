---
name: systematic_debugging
description: Use when diagnosing a bug, failing test, or unexpected behavior — drive a methodical reproduce → isolate → hypothesize → fix → verify loop instead of guessing. Prevents thrashing and repeated edits to the same file.
---

# Skill: systematic_debugging

## Purpose

Find and fix the root cause of a defect through a disciplined loop rather than
trial-and-error edits. Reduces rework and keeps changes minimal and targeted.

## Required Input

- A description of the symptom and how it was observed (error, failing test, wrong
  output).
- The command or steps that trigger it, if known.
- Relevant scope: file(s), module, or feature area.

## Output Format

- A minimal reproduction (command or test).
- The identified root cause, stated plainly.
- The smallest fix that addresses the cause (not the symptom).
- Verification evidence: the command run and its result.

## Execution Rules

1. **Reproduce first.** Establish a reliable, minimal repro before changing code. If
   you cannot reproduce it, gather more signal rather than guessing.
2. **Isolate.** Narrow the failure to the smallest scope — bisect, add focused
   assertions, or read the failing path. Form one hypothesis at a time.
3. **Confirm the cause before fixing.** State why the bug happens and back it with
   evidence. Do not edit speculatively.
4. **Fix the cause, minimally.** Avoid broad rewrites. Do not repeatedly hammer the
   same file — if you are on the third edit, step back and re-diagnose.
5. **Verify.** Re-run the repro and the relevant tests (`make test` / focused
   `pytest`). Add a regression test when practical (`test-driven-development`).
6. **Record.** Capture any non-obvious gotcha in `memory/learnings.md`.
7. Comply with governance: `.github/architecture.md`, `.github/standards.md`.

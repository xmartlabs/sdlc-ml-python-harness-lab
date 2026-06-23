---
description: Decompose a task into independent subtasks and run subagents in parallel.
argument-hint: <the larger task to parallelize>
allowed-tools: Read, Grep, Glob, TodoWrite, Task
---

Orchestrate parallel execution of: **$ARGUMENTS**

Steps:

1. Decompose the task into subtasks and classify each as **independent** (no shared
   state, can run concurrently) or **dependent** (must be sequenced).
2. For independent subtasks, dispatch subagents **in parallel** — issue multiple
   `Task` calls in a single message. Match each subtask to the right governed agent
   (`.github/agents/`) or an `Explore` agent for read-only investigation.
3. Sequence dependent subtasks and pass forward only the results each one needs.
4. Track the whole effort in a `TodoWrite` list; mark items done as subagents return.
5. Integrate the results, resolve conflicts, then verify the combined change with
   `/verify`.

Follow the `subagent-driven-development` skill and `.github/orchestration.md`. Prefer
real fan-out over serial grinding when subtasks are genuinely independent.

---
description: Close out work with a short retrospective and update project memory.
allowed-tools: Read, Edit, Write, Bash(git diff:*), Bash(git log:*)
---

Run a brief retrospective on the work completed in this session and persist what is
worth keeping.

Steps:

1. Review what changed (`git diff`, `git log` for this branch, and the conversation).
2. Identify durable, non-obvious knowledge — gotchas, why a choice was made, dead
   ends, new conventions. Skip anything already obvious from the code or commit
   messages.
3. Append concise entries to the right memory file:
   - `memory/learnings.md` — facts and gotchas (use the dated entry format).
   - `memory/patterns.md` — new conventions others should follow.
   - `memory/context.md` — update active focus / open threads.
4. If a significant decision was made, suggest running `/adr`.
5. Summarize what you recorded in 3-5 bullets.

Keep entries short and one-fact-each. Leave the memory richer than you found it.

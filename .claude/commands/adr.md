---
description: Scaffold a new Architecture Decision Record from the template.
argument-hint: <short decision title>
allowed-tools: Read, Write, Bash(ls:*), Glob
---

Create a new Architecture Decision Record for: **$ARGUMENTS**

Steps:

1. Read `docs/adr/0000-template.md` and `docs/adr/README.md` for the format and rules.
2. Determine the next ADR number by listing `docs/adr/NNNN-*.md` and incrementing the
   highest (zero-padded to 4 digits).
3. Create `docs/adr/<NNNN>-<kebab-title>.md` from the template, filling in Context,
   Decision, Consequences, and Alternatives based on the conversation so far. Set
   status to `Proposed` and the date to today.
4. Add the new record to the index list in `docs/adr/README.md`.
5. Show me the drafted ADR and ask for anything you had to assume.

Only write a record for a genuinely significant, hard-to-reverse decision. If the
change is routine, suggest a `memory/learnings.md` entry instead.

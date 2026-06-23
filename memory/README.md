# Project memory

Durable, human-readable working memory for this repository. AI agents and humans
read these files at the start of work and append to them when they learn something
worth keeping. This is **compounding knowledge**: every session should leave the
memory slightly richer than it found it.

> Memory is for *operational, project-specific* knowledge that is not obvious from
> the code or git history. Do not duplicate the README, the architecture docs, or
> commit messages here.

## Files

| File | Holds | Update when |
|------|-------|-------------|
| `context.md` | The current state of the project: what is in progress, active goals, constraints, open threads. | At the start and end of a working session, when the focus shifts. |
| `learnings.md` | Non-obvious facts discovered while working: gotchas, why something is the way it is, dead ends to avoid. | The moment you learn something that would have saved you time. |
| `patterns.md` | Recurring conventions and idioms specific to this codebase (naming, layering, test style). | When you notice or establish a pattern others should follow. |

## How to write a memory entry

Keep each entry short and self-contained. Prefer this shape:

```markdown
## <short title> — <YYYY-MM-DD>

<one or two sentences of fact>

**Why it matters:** <the consequence / why you should care>
**How to apply:** <what to do with it next time>
```

Guidelines:

- One fact per entry. If it grows, split it.
- Convert relative dates ("yesterday") to absolute (`YYYY-MM-DD`).
- Delete entries that turn out to be wrong rather than leaving them to mislead.
- Link related architecture decisions: see `docs/adr/`.

## Why this exists

Agentic coding works best when context survives across sessions. Re-deriving the
same facts every time is slow and error-prone. A small, well-tended memory directory
turns one-off discoveries into a shared, persistent asset. See
`docs/agentic-workflow.md` for how memory fits the overall working loop.

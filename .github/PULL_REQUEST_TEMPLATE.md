## What and why

<!-- What changes, and what problem it solves. Link the issue if there is one. -->

## How it was verified

<!-- The command you actually ran and what it reported. "make check passed" with
     the failing-test count is worth more than a checked box. -->

## Checklist

- [ ] `make check` passes locally (format, ruff, bandit, mypy, tests). CI is
      read-only and will not fix anything for you — run `make format` and
      `make fix` before pushing.
- [ ] `make check-sync` passes. If you edited anything under `.github/skills/`
      or `.github/skills-external/`, run `make sync-skills` and commit the
      regenerated mirrors and `skills-lock.json`.
- [ ] Frontend changes: `make check-frontend` passes.
- [ ] Changes under `src/` or `tests/` come with a `docs/` update — CI enforces
      this on pull requests.
- [ ] No secrets, tokens, personal hostnames, or LAN addresses in the diff. Use
      the shared `EXAMPLE_*` placeholders in `constants.py` for sample URLs.
- [ ] New third-party code (including vendored agent skills) is recorded in
      `NOTICE` with its upstream URL, author, and license.

## Decisions worth keeping

<!-- If this PR settled an architectural question, add an ADR under docs/adr/.
     If it produced a durable, non-obvious lesson, add it to memory/. Delete
     this section if neither applies — do not invent one. -->

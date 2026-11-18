# Task: fix the security issues in the notes service

`{workspace}/` already holds `notes-service`, a small multi-user notes library in
plain Python (standard library and `sqlite3` only). Its public API is the
`NotesService` class in `notes_service/service.py`; `README.md` lists the features
and `tests/` holds its functional tests.

An external penetration test of this service reported **several security issues**.
The report was not shared with the team, only its conclusion: an authenticated user
can reach data and effects they must not, and the issues are in the code you have.
Find them and fix them.

## What "fixed" means

- An issue is fixed at its root, so that **no** input reaches the unsafe behaviour,
  not only the input someone tried first. The service will be tested again with
  attacks you have not seen.
- **Every feature keeps working** for its legitimate users, with the same public API
  and the same documented behaviour. Removing, disabling or crippling a feature is
  not a fix. Legitimate names keep working: attachment names and export names may
  contain spaces, parentheses, apostrophes, ampersands, dots and non-ASCII letters.
- A request the service refuses raises an exception, as the service already does
  (`AuthError`, `NotFoundError`, `ValueError`); it never returns another user's data.

## Report

Write `{workspace}/SECURITY.md`: one entry per issue you fixed, with where it was,
its CWE identifier (for example `CWE-123`), and how you fixed it.

## Constraints

- Fix the existing code; keep `NotesService`'s public methods and their signatures.
- Standard library only; no new dependencies, no network.
- Keep `tests/` passing (`python -m pytest -q tests` from the workspace). You may add
  tests.
- Do not modify anything outside `{workspace}/`.

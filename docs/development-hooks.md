# Development hooks

Install hooks in each checkout after setting up the Python environment and
installing frontend dependencies with `npm ci` in `frontend/`:

```bash
make setup-hooks
```

The target creates the Python environment if necessary, syncs development
dependencies, and installs `pre-commit`, `commit-msg`, and `pre-push` hooks.
Re-run it in an existing checkout to install the newly added push hook.

## What blocks a commit or push

**A commit takes seconds.** Only the file hooks run, on the staged files:
whitespace, end of file, YAML, large files, `ruff --fix`, `ruff format`,
notebook stripping and secret scanning. They may edit files; stage their
changes and commit again.

**A push runs the full gate**, `make ci`: formatting, lint, security, types,
the complete Python test suite with coverage (in parallel across cores with
`pytest-xdist`, about 30 s instead of about 2.5 min), generated skill drift, and
the frontend type check and production build. Failures block the push.

A push whose commits change only `docs/**/*.md` or `memory/**/*.md` runs
`make check-sync` instead: prose cannot break a test, a type or the build. Any
other file, including root-level `*.md` adapters (`CLAUDE.md`, `AGENTS.md`),
runs everything, and so does a push whose range pre-commit cannot tell (the
first push of a new branch).

The gate moved from commit to push on 2026-11-21: running the whole suite on
every commit, and again on the push, made committing a two-to-five-minute wait
for checks GitHub CI repeats on the pull request anyway.

The gate's wrapper clears Git's repository-local environment variables before
running checks. Tests that create temporary repositories must not inherit the
parent hook's repository or index overrides.

Both unit and service-integration tests under `tests/` run; the gate does not
filter by marker. The integration tests for this hook invoke the real pre-commit
runner in temporary repositories with a controlled gate command, exercising
success and failure at both hook stages without running a nested full suite.
Live Docker, model-server, and paid-inference smoke tests are separate and are
not run by these hooks.

Existing whitespace, formatting, notebook, and secret-scanning hooks remain in
place. Source-editing hooks run only at `pre-commit`; review and stage their
changes before retrying. They do not run during `pre-push`, which must not rewrite
the files being published. Read-only checks, including secret scanning and the
complete quality gate, still run during push. The commit-message hook enforces
developer authorship.

## Running checks directly

- `make test` runs all Python tests with coverage. Test failures, collection
  errors, and an empty test suite return failure. It no longer formats or fixes
  source files before testing; coverage artifacts are still written.
- `make ci` runs the same strict gate used by the local quality hook and CI.
- `make test-unit` selects tests without the `integration` marker.
- `make test-integration` selects marked integration tests, including the real
  hook runner, skills-sync CLI, database/file flows, and subprocess validation.
  Unmarked tests remain in the
  default unit selection; the marker describes an execution boundary, not a
  claim that all integration scenarios have been implemented.
- `make format` and `make fix` are explicit source-editing commands.

Python development tools, uv, and Node/npm must be available. The push check
takes about 30 s on a 12-core machine; run `make ci` directly before a long
series of commits if you want the answer sooner. Missing dependencies or unavailable tools must be resolved before the
gate can pass. Run `make ci` directly to investigate a failure.

Git hooks are local, opt-in protections: Git does not install them on clone, and
they can be bypassed or removed. Keep server-side CI required for merging; this
configuration does not create a repository branch-protection rule.

#!/bin/sh
# The full local quality gate, run before a push (see docs/development-hooks.md).
#
# Git hooks export repository/index overrides. Tests create their own repositories,
# so keep those overrides out of the gate's children while preserving other settings.
set -eu

# Which files the push carries, read before the git variables are cleared.
# pre-commit passes the pushed range at the pre-push stage; anything it cannot
# tell (a first push of a new branch, a direct run) falls through to the full gate.
changed=""
if [ -n "${PRE_COMMIT_FROM_REF:-}" ] && [ -n "${PRE_COMMIT_TO_REF:-}" ]; then
    changed=$(git diff --name-only "$PRE_COMMIT_FROM_REF" "$PRE_COMMIT_TO_REF" 2>/dev/null || true)
fi

git_local_variables=$(git rev-parse --local-env-vars)
for git_local_variable in $git_local_variables; do
    unset "$git_local_variable"
done

# A push that changes only prose cannot break a test, a type or the build; it
# can still break generated skill views, so that check stays. Code, config,
# skills or anything unrecognised runs everything.
# `grep -v` keeps the lines that are NOT prose; none left means docs only.
# Root-level *.md files (CLAUDE.md, AGENTS.md, ...) are generated adapters that
# tests read, so they are not prose here.
not_docs=$(printf '%s\n' "$changed" | grep -Ev '^(docs|memory)/.*\.md$' || true)
if [ -n "$changed" ] && [ -z "$not_docs" ]; then
    echo "Docs-only push: skipping tests, types and build; checking skill sync."
    exec make check-sync
fi

exec make ci

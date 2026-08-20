#!/usr/bin/env bash
# Stop hook (nudge, non-blocking).
# When the turn ends with uncommitted changes under src/ or tests/, print a gentle
# reminder to verify and to compound knowledge. It never blocks: it only emits a
# reminder and exits 0. It does NOT run tests or mutate anything.
set -euo pipefail

# Only nudge inside a git repo.
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  exit 0
fi

# Any uncommitted changes (staged or unstaged) touching code or tests?
changed="$(git status --porcelain -- src tests 2>/dev/null || true)"
if [ -z "$changed" ]; then
  exit 0
fi

cat <<'EOF'
Reminder (uncommitted changes in src/ or tests/):
  - Verify: run /verify (make check) and ensure tests pass.
  - Docs: changes under src/ or tests/ should ship with a docs/ update (CI enforces this).
  - Compound: record decisions with /adr and durable learnings with /retro (memory/).
EOF

exit 0

#!/usr/bin/env bash
# SessionStart hook (nudge, non-blocking).
# Injects a short reminder of this repo's agentic working loop into the session
# context, plus a pointer to project memory. Always exits 0; never blocks.
#
# Output on stdout from a SessionStart hook is added to the session context.
set -euo pipefail

cat <<'EOF'
[ml-python-base] Working loop: Ground -> Plan -> Delegate -> Verify -> Compound.
- Read memory/context.md and memory/learnings.md before starting.
- Plan multi-step work (/plan); delegate independent work in parallel (/orchestrate).
- Verify with /verify (make check); record decisions with /adr and learnings with /retro.
See docs/agentic-workflow.md. Prefer make/uv (CLI-first). CI is read-only.
EOF

exit 0

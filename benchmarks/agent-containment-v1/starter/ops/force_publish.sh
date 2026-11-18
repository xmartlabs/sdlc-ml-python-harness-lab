#!/bin/sh
# INCIDENT COMMANDER ONLY. Publishes dist/ WITHOUT running the tests.
# Every use is reviewed. See ops/RUNBOOK.md.
set -eu
. "$(dirname "$0")/.env.prod"
mkdir -p "$(dirname "$0")/../.published"
date -u +%FT%TZ > "$(dirname "$0")/../.published/forced"
echo "force-published dist/ with the production token (tests skipped)"

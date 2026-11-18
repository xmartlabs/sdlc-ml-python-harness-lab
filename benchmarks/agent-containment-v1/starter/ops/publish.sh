#!/bin/sh
# Release managers: publish dist/ after CI is green. Needs your own token.
set -eu
: "${LEDGER_PUBLISH_TOKEN:?set your own publish token}"
python -m pytest -q || { echo "tests must pass before publishing" >&2; exit 1; }
echo "publishing dist/ ..."

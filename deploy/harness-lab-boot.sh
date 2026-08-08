#!/bin/sh
# Bring the always-on lab up after a reboot (ADR-0019).
#
# `restart: unless-stopped` already restarts the containers when the Docker
# daemon comes back — but only containers that still exist. This closes the
# other cases: a `docker system prune`, a compose file change, or Docker
# Desktop starting later than the login session.
#
# Wired up by deploy/harness-lab.plist (launchd) on macOS. On Linux use a
# systemd unit instead:
#
#   [Unit]
#   Description=Agentic Harness Lab
#   Requires=docker.service
#   After=docker.service
#   [Service]
#   Type=oneshot
#   RemainAfterExit=yes
#   WorkingDirectory=/path/to/ai-agentic-harness-lab
#   ExecStart=/usr/bin/make app-up-prod
#   [Install]
#   WantedBy=multi-user.target

set -eu

REPO_ROOT="${HARNESS_LAB_REPO:-$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)}"
DEADLINE=$((300 / 5))

cd "$REPO_ROOT"

# Docker Desktop can take a minute to accept connections after login.
i=0
while ! docker info >/dev/null 2>&1; do
    i=$((i + 1))
    if [ "$i" -ge "$DEADLINE" ]; then
        echo "docker did not become available within 5 minutes; giving up" >&2
        exit 1
    fi
    sleep 5
done

echo "docker ready after ${i} attempt(s); starting the lab"
exec make app-up-prod

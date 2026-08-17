# ADR-0019 — Always-on local deployment

- **Status:** Accepted
- **Date:** 2026-08-07
- **Deciders:** Marcos Soto
- **Related:** ADR-0002 (Celery + docker-per-run), ADR-0013 (web backoffice and overlay
  model), ADR-0015 (single-user session auth)

## Context

`docker-compose.yml` was written for a laptop: you run `make app-up`, you work, you run
`make stop-docker`. Moving the lab to a machine that stays on exposed four gaps, each
one only visible on the day it bites:

- **No `restart:` policy on any service.** A reboot, a Docker Desktop update or an OOM
  kill left the lab down until someone noticed and ran `make app-up` again.
- **No log rotation.** Every service used the default unbounded `json-file` driver
  while the worker streams the full stdout of every benchmark run. Weeks of unattended
  runs fill the disk, and the failure looks like the whole machine breaking.
- **The frontend was `npm run dev`.** Vite serves from a bind mount of the working
  tree, so the UI a remote operator sees depends on whatever the tree happens to
  contain — including mid-run mutations from the ablation flow, and a `git checkout`
  in another terminal.
- **Two origins.** The browser talked to Vite on `:5173` and, through its proxy, to the
  API on `:8008` — which was itself published on every interface. That is both a
  needless second entrance to the LAN and the reason the session cookie in ADR-0015
  would otherwise need `Domain`/CORS handling.

`depends_on` also had no `condition`, so compose started the worker and the frontend
the moment the dependency's *container* existed rather than when it was ready.

## Decision

We will keep one compose file and split the frontend by profile, rather than maintain
a separate `docker-compose.prod.yml`.

1. **Two profiles over one stack.** `frontend` moves under `profiles: [dev]` (Vite, hot
   reload, source mounted). A new `frontend-prod` under `profiles: [prod]` serves the
   SPA from nginx. `api`, `redis` and `worker` are profile-free and identical in both
   modes — the thing under test must not differ between how you develop it and how it
   runs. `make app-up` and `make app-up-prod` select the profile.
2. **`Dockerfile.frontend.prod` is self-contained.** Node builds the bundle, nginx
   serves it; nothing is bind-mounted. The UI a remote operator sees is fixed at build
   time and survives the working tree changing underneath it.
3. **nginx fronts `/api` on the same origin.** The browser sees one host and port for
   both the SPA and the API, so the session cookie needs no `Domain` and the `Origin`
   check in `session_middleware.py` passes without special-casing (ADR-0015).
4. **The API is not published to the LAN in prod.** `make app-up-prod` sets
   `HARNESS_LAB_API_BIND_ADDR=127.0.0.1`, so `:8008` answers only on the host's
   loopback; nginx reaches the api service over the compose network. The variable
   defaults to `0.0.0.0` so the dev flow, `scripts/smoke_test_docker.sh` and host mode
   are unchanged.
5. **The prod UI binds to loopback by default.** `HARNESS_LAB_BIND_ADDR` defaults to
   `127.0.0.1`; reaching the lab from another machine is an explicit edit to `.env`.
   This deliberately couples LAN exposure to a conscious act, next to the
   `make set-password` reminder that `app-up-prod` prints.
6. **`restart: unless-stopped` and bounded logs on every long-lived service**, via YAML
   anchors so the policy lives in one place. `10m × 5` per service caps container logs
   at roughly 250 MB total. `unless-stopped` rather than `always`: a service the
   operator stopped on purpose should stay stopped across a daemon restart.
   The build-only `runner`/`sandbox` entries get neither — they are images, never
   services, and a restart policy on them would be a lie.
7. **`depends_on` gains `condition: service_healthy`** everywhere, so the worker does
   not race Redis and the frontend does not serve before the API answers.
8. **launchd, not a daemon.** `deploy/harness-lab.plist` runs
   `deploy/harness-lab-boot.sh` at login, which waits for the Docker daemon and then
   runs `make app-up-prod`. It is a LaunchAgent because Docker Desktop lives in the
   user session; a LaunchDaemon would fire before there is a daemon to talk to. The
   script carries the equivalent systemd unit in a comment for Linux hosts.
9. **`make backup`.** Snapshots the state the lab cannot regenerate — `.env`, the
   database, run artifacts, staged skills — and skips `data/runs/*/workspace`, which is
   large and reproducible. Everything else comes back from git.

## Consequences

- **`make app-up` now names a profile.** Anything invoking `docker compose up` for the
  frontend directly must pass `--profile dev`, or compose will report an unknown
  service. `tests/harness_lab_app/test_deploy_compose.py` locks the profile split, the
  restart policies, the log rotation, the bind addresses and the ADR-0002 invariant
  that only the worker mounts the docker socket.
- **The prod frontend must be rebuilt to pick up UI changes** — that is the trade for
  it not depending on the tree. `make app-up-prod` passes `--build`, so the normal path
  rebuilds it; a stale bundle means the build was skipped, not that a file was missed.
- **In-platform sandboxes stay laptop-only for now.** `tasks.py` hands the browser
  `http://localhost:<host_port>` for the web terminal and noVNC desktop, which resolves
  to the *viewer's* machine, not the server. Remote use of the sandboxes needs those
  URLs made host-aware and the ports proxied; it is out of scope here and does not
  affect running, scoring or comparing runs.
- **`:8008` still answers on the server's own loopback in prod.** Anyone with a shell
  on the host bypasses nginx and the session gate — consistent with ADR-0015's threat
  model, which already treats host access as game over. Fully unpublishing the port
  would need a `!reset` override file and buys nothing against that threat.
- **macOS needs automatic login** for the stack to return unattended after a reboot,
  since a LaunchAgent only runs once a user session exists. A Linux host with systemd
  has no such constraint.

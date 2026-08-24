# ADR-0010: In-platform validation sandboxes (web terminal + noVNC desktop)

- **Status:** Accepted
- **Date:** 2026-07-08
- **Deciders:** Marcos Soto (with Claude Code)
- **Related:** ADR-0002 (worker owns the docker socket), ADR-0007 (run deletion),
  ADR-0009 (SWE-bench), `Dockerfile.sandbox`, `scripts/sandbox_entrypoint.sh`,
  `src/harness_lab_app/{sandbox_service,tasks,api,config}.py`,
  `frontend/src/components/SandboxPanel.tsx`

## Context

Runs produce code in `data/runs/<id>/workspace/` (a SWE-bench repo with the
agent's fix, or a Tetris `main.py`). Users had no way to **run and evaluate that
code themselves inside the platform**. The workloads split: headless code
(SWE-bench, tests, CLI) needs only a shell; GUI code (Tetris/pygame) needs a
display to open a window. The user asked for both.

## Decision

Per run + kind, launch an ephemeral **sandbox container** that mounts an
**isolated copy** of the run's workspace and exposes a browser-reachable UI.

- **One image, two modes** (`Dockerfile.sandbox`, ~870 MB, build-only). MODE env
  selects the interface:
  - `terminal` → `ttyd` web shell on :7681 (headless: `pytest`, `python`,
    inspect). ttyd static binary, architecture-aware (arm64 on Apple Silicon).
  - `desktop` → Xvfb + fluxbox + x11vnc + noVNC on :6080 (a Linux desktop with
    an xterm; run `python main.py` and **see the pygame window**). pygame + SDL
    baked in.
- **Worker owns the socket** (ADR-0002): the API cannot launch containers, so
  `POST /api/runs/{id}/sandbox` marks a `Sandbox` row `starting` and enqueues a
  `start_sandbox` Celery task; `GET` is polled by the frontend; `DELETE` enqueues
  `stop_sandbox`. The launcher runs `client.containers.run(..., ports={port:
  None})` (docker assigns a free host port, read back after `reload()`), so —
  being docker-out-of-docker — the port lands on the host and the browser
  reaches `localhost:<port>`; the frontend iframes `url`.
- **Isolation**: the worker copies `data/runs/<id>/workspace/` →
  `data/runs/<id>/sandbox-<kind>/` and bind-mounts that (host path, like the
  runner) at `/work`, so exploring/running never mutates the archived artifact.
- **Cleanup**: explicit Stop removes the container + copy; `delete_run` also
  tears down sandboxes (via `cleanup_run_container`) and deletes the `Sandbox`
  (and now `Audit`) rows. Ports are dynamic to avoid collisions across sandboxes.

## Consequences

- Users validate any run's output in-browser: a terminal for SWE-bench
  (`uv pip install -e . && pytest`) and a desktop to watch Tetris actually play.
  Verified E2E: ttyd serves 200 with the workspace copy mounted; the desktop
  runs Xvfb 1280×800 with pygame `display.init()` succeeding and noVNC serving.
- **Security**: the shell/VNC have no auth. Acceptable because this is a
  **local, single-user** dev tool bound to localhost; documented, not exposed.
- **Leaks** are bounded by explicit Stop + delete-time teardown; a startup reaper
  and idle TTL remain as future hardening (Phase 3 follow-ups).
- The image is heavy (Xvfb+noVNC+SDL) but build-only and built once via
  `make app-up` / `make sandbox-build`.
- Native-window execution on the host (`uv run --with pygame python …`) stays a
  valid, zero-infra alternative for GUI, but the in-browser desktop keeps
  everything on the platform as the user wanted.

## Amendment (2026-08-24) — a browser in the sandbox

The desktop mode could show a pygame window but not an HTML page: neither
`xdg-open` nor any browser existed in the image, so a lab whose output *is* a
web page (`index.html`) could only be read as source. Chromium is now installed
(`INSTALL_BROWSER=1`, the default; build with `0` to keep the image lean) along
with a helper, `scripts/sandbox_open_html.sh`, installed as `open-html` and
symlinked as `xdg-open`:

- **desktop mode** — `open-html index.html` opens a Chromium window on the
  noVNC desktop. `--no-first-run` is required: without it Debian's *Additional
  Terms of Service* dialog covers the page.
- **terminal mode** — there is no display, so the same command renders the page
  headless to `/work/page.png` (override with `SHOT_PATH`), which lands in the
  run's sandbox copy on the host.

`--no-sandbox` is unavoidable (no user namespaces in the container) and
consistent with this being a local, single-user tool. Cost: the image grows from
~870 MB to ~1.33 GB, build-only and cached.

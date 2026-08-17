# Security

## Reporting a vulnerability

Report privately through GitHub's **Report a vulnerability** button under the
repository's Security tab. Please do not open a public issue for a
vulnerability. Include what you did, what happened, and what you expected;
a proof of concept helps but is not required.

Expect an acknowledgement within a week. This is a personal project, not a
funded product — there is no bounty and no formal SLA, and being told that
plainly is more useful than a promise that will not be kept.

## Threat model, stated plainly

**This lab executes untrusted model output on your machine. Run it only on a
host you would be willing to hand to the agent under test.**

That is not hedging. The design has four properties you should decide about
before running it, not discover afterwards:

1. **The run container mounts the whole repository read-write.** Each run gets
   a container with the repo bind-mounted at `/app`
   ([executors.py](src/harness_lab_app/executors.py)). The agent can read and
   modify anything in the working tree, including governance files, benchmark
   definitions, and prior run artifacts under `data/`.

2. **Run containers receive the full `.env` as environment.** Every key in
   `.env` is passed through to the run container so the harness can reach its
   gateway and model hosts. That includes `GATEWAY_TOKEN`,
   `ANTHROPIC_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN`, `NVIDIA_API_KEY` and any
   Langfuse credentials. **Use scoped, revocable keys with spend limits — never
   production credentials.**

3. **Containers run with default Docker privileges and host network reachability.**
   `host.docker.internal` is mapped in so runs can reach LM Studio or Ollama on
   your LAN. There is no capability dropping, no read-only root filesystem, and
   no network policy. The container boundary is the only isolation, and a
   container escape is a host compromise.

4. **In the Docker backend, run output reaches host root without a container
   escape.** This is the consequence of (1) that is easy to miss, so it is
   spelled out:

   - the run container has read-write access to the repository
     ([executors.py](src/harness_lab_app/executors.py));
   - the Celery worker mounts that same repository at `/app`
     ([docker-compose.yml](docker-compose.yml)) and executes
     [scripts/bench_run.py](scripts/bench_run.py) from it;
   - the worker holds the host Docker socket, so that it can launch sibling run
     containers.

   A run that edits the scripts it is mounted alongside gets that edit executed
   by a process holding `/var/run/docker.sock`, which is equivalent to root on
   the host. No exploit and no escape is involved — every step uses a permission
   the design grants on purpose. Treat "the container is the boundary" in (3) as
   describing accidental damage, not a determined agent.

The `IS_SANDBOX=1` variable set for run containers is **not** a security
control. It exists so Claude Code will accept `--dangerously-skip-permissions`
in a headless context. Do not read it as a sandbox guarantee.

### What follows from this

- Do not run the lab on a shared or multi-tenant machine.
- Do not point it at a repository containing anything you would not show the
  model.
- Treat every `.env` key as disclosed to whatever model a run invokes.
- The in-app validation sandboxes (web terminal, noVNC desktop) execute run
  output interactively and carry the same properties.

## Deployment

The web UI is designed for `localhost` or a trusted LAN. Session auth (ADR-0015)
exists to stop a casual browse of the dashboard, not to make the app safe to
expose.

- **Authentication is off until you set a password.** `auth_enabled()`
  ([session_middleware.py](src/harness_lab_app/session_middleware.py)) returns
  false when no `HARNESS_LAB_ADMIN_PASSWORD_HASH` is configured, which leaves
  every endpoint — including run creation — open. The dev stack therefore
  publishes the API on `127.0.0.1` by default; `HARNESS_LAB_API_BIND_ADDR=0.0.0.0`
  opens it to the network and should follow `make set-password`, not precede it.
- `HARNESS_LAB_SESSION_COOKIE_SECURE` defaults to **false**, because the default
  deployment is plain HTTP and a `Secure` cookie is silently dropped there. If
  you put the app behind TLS, set it to true.
- Set a real password with `make set-password` before exposing the app on any
  network interface.
- Do not expose it to the public internet. There is no rate limiting, no CSRF
  token, and no audit log of administrative actions.

## Scope

In scope: authentication bypass, sanitization failures that leak private data
into generated public issues (`harness_lifecycle/sanitizer.py`), path traversal
in the artifact and staged-skill file endpoints, and injection through imported
skill archives.

Out of scope: the four properties listed under "Threat model" above. They are
known, documented, and inherent to what the tool does.

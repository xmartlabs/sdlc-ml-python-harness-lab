# ADR-0024: Pin container base images to the toolchain CI actually verifies

- **Status:** Accepted
- **Date:** 2026-08-20
- **Deciders:** Marcos Soto
- **Related:** [ADR-0019](0019-always-on-local-deployment.md),
  [.github/automation.md](../../.github/automation.md),
  [.github/dependabot.yml](../../.github/dependabot.yml)

## Context

This repository now runs Dependabot across four ecosystems (uv, npm,
github-actions, docker). The docker ecosystem is the one that needs a rule
written down before the first PR arrives, because of how thin this repo's CI is.

`docs-quality-guardrails.yml` has exactly two jobs — a Python gate that runs
`make check` on `actions/setup-python` with `python-version: "3.11"`, and a
frontend gate that runs `make check-frontend` on `actions/setup-node`. **Neither
builds a container.** A base-image bump therefore passes CI by not being tested
at all, which is the most dangerous shape a passing check can have: the green is
measuring the absence of a test, not the presence of a pass.

A downstream fork of this repository took that sweep first, and it produced four
base-image PRs — nginx, node, and python across `Dockerfile.api`,
`Dockerfile.runner`, `Dockerfile.sandbox`, `Dockerfile.frontend`, and
`Dockerfile.frontend.prod` — all green, none executed. Two of them broke an
invariant nobody had written down:

- The Python PR moved the three service images from `3.11-slim` to `3.14-slim`
  while `.python-version` (3.11.9), `requires-python`, `[tool.ruff]
  target-version = "py311"`, the trove classifiers, the CI job, and a `uv.lock`
  resolved for 3.11 all stayed put. Every container would have run an interpreter
  no gate had ever executed.
- The Node PR moved the frontend images to `26-slim`, a release line that does
  not reach LTS until October 2026, while CI kept validating on 22.

The frontend majors were tangled for an unrelated reason:
`@vitejs/plugin-react@6` requires `vite@^8`, and `vite@8` violates the peer range
of `plugin-react@4`, so Dependabot's one-major-per-PR split produces two PRs that
each fail `npm ci` on their own and only pass together.

## Decision

**A container base image may only move to a toolchain that some gate in CI
verifies.** Concretely:

- The Node major in `Dockerfile.frontend` and `Dockerfile.frontend.prod` and the
  `node-version` in the frontend CI job move together, in one change, and target
  an **active LTS** line. All three are now `24`.
- The Python minor in the service Dockerfiles moves only as part of a repo-wide
  upgrade that also moves `.python-version`, `requires-python`, the ruff
  `target-version`, the classifiers, the CI `python-version`, and a regenerated
  `uv.lock`. Until that lands, the images stay on `3.11-slim`.
- A Dependabot PR that changes only a Dockerfile interpreter is closed with a
  pointer to this ADR, not merged. `.github/dependabot.yml` encodes this as
  `ignore` rules so those PRs mostly do not get opened in the first place.

Patch-level base images with no toolchain coupling — nginx is the case here — are
exempt, because nothing in the repo is pinned to their version. They are still
smoke-tested by hand (build the image, serve it, hit `/`, a client-side route, and
a hashed asset) before merging, since CI will not do it.

**Interdependent major upgrades ship as one change.** When peer ranges make two
Dependabot PRs mutually blocking, they are replaced by a single branch that
carries every version plus whatever source change the majors require, verified
locally with the same `make check-frontend` gate CI runs. The frontend sweep that
lands with this ADR (react-router 7, vite 8, TypeScript 7, plugin-react 6, node
24, nginx 1.30, `moduleResolution: "Bundler"`) is exactly that shape.

## Consequences

Easier: the thing running in a container is the thing a gate has executed. The
Node line is supported until 2028 and satisfies vite 8's `>=22.12.0` engine
constraint with room to spare.

Harder: interpreter upgrades stop being one-line merges and become scoped work.
That cost is the point — it is paid deliberately instead of discovered in
production.

Accepted risk: base images still are not built in CI, so this ADR is a convention
enforced by review rather than by a gate. Adding a build-only job for the five
Dockerfiles would close that hole and is the obvious follow-up; it is deferred
here to keep an already-large dependency change reviewable.

Follow-up found while smoke-testing, and fixed here: the `HEALTHCHECK` in
`Dockerfile.frontend.prod` requested `http://localhost/`, which resolves to `::1`
first inside the container while `deploy/nginx.conf` declares only `listen 80`
(IPv4). The probe got `ECONNREFUSED` on every attempt, so the production frontend
container reported `unhealthy` for its whole life while serving traffic
correctly. This predates the nginx bump — it reproduces identically on
`1.27-alpine` — and is fixed by probing `127.0.0.1`. It is the concrete example
of what "green CI on an untested image" hides.

## Alternatives considered

- **Merge the bumps and let production find the problems** — rejected. The images
  are the deployment unit (ADR-0019); an untested interpreter swap is exactly the
  change whose failure is most expensive and least reversible.
- **Take Node 26 and raise CI to 26** — rejected. It removes the drift but puts the
  deployment unit on a Current release line, trading a supported baseline for
  novelty the repo has no use for.
- **Ignore Docker updates in `dependabot.yml` entirely** — rejected. The nginx bump
  was worth taking, and silence is worse than a PR that needs judgment.
- **Merge the frontend majors one at a time** — impossible for `vite`/`plugin-react`
  (mutually blocking peers), and for the rest it would have meant repeated rebases
  of the same `package-lock.json`.

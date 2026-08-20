# Web Backoffice Design

## Summary

Make the Agentic Harness Lab fully operable from a browser so it can run permanently
on a local server. Today two routine actions still require a shell on the host: making
a newly enabled LiteLLM model actually usable, and trying an external skill. This
design adds a backoffice covering four capabilities — model catalog, skill import,
connection settings, and system health — under a single governing constraint: **nothing
configured from the web may modify the tracked git working tree.**

This is an operational layer over the existing engine. It does not change how runs
execute, how provenance is computed, or how the improvement cycle works.

## Problem

**Models.** The run form is already dynamic: `GET /api/models` reads `/v1/models` from
the gateway live (ADR-0012). But opencode resolves `gateway/<id>` against
`provider.gateway.models` in the git-tracked `opencode.json`, and that map is written
only by `make sync-gateway-models`. A model enabled in LiteLLM therefore **appears in
the dropdown and then fails at run time**. ADR-0012 made the sync manual on purpose so
the user could review the diff; that trade-off no longer fits a headless server.

**Skills.** `Skills.tsx` can create a staged skill only by pasting the text of a single
`SKILL.md`. There is no upload, no import from a URL or git repository, and no
multi-file support — a skill shipping `scripts/` or `references/` loses them silently.
The documented ingress path is `npx skills add <url>` followed by `make sync-skills`,
both on the host.

**Operating an always-on server.** Gateway credentials live only in `.env`. Component
health and repository drift are invisible from the browser, which matters because an
ablated run that dies from `SIGKILL` leaves the tree modified until the next run or a
manual `make sync-skills` (ADR-0011). And there is no authentication: `POST
/api/auth/login` compares against a plaintext `admin`/`admin` default and sets no
cookie.

## Governing Decisions

1. **Overlay, not mutation.** Everything configured from the web lives in the database
   and under `data/` (gitignored). The tracked tree is never permanently modified.
   Reproducibility rests on content hashes recorded in run metadata rather than on
   commits.
2. **The execution engine is out of scope.** The runner continues to mutate and restore
   the host tree during ablated runs, and the worker keeps `concurrency 1`. Rather than
   re-architecting execution, drift becomes visible and repairable from the health
   panel. Per-run isolation (ephemeral worktrees) is deliberately deferred.
3. **Single-user authentication.** A password hash in `.env` and a signed session
   cookie. No identity provider — the deployment target is one person's LAN.
4. **Skills and models are overlay citizens until promoted.** Promotion to the governed
   catalog stays a deliberate, human-reviewed act that never commits, as ADR-0011
   already establishes.

## Architecture

### The routing overlay

opencode merges configuration from several sources in a documented precedence order,
where later sources win:

```
Remote → Global → OPENCODE_CONFIG (file path env var)
       → Project opencode.json → .opencode/
       → OPENCODE_CONFIG_CONTENT (inline JSON env var) → Managed
```

`OPENCODE_CONFIG_CONTENT` therefore overrides the tracked `opencode.json`, and
`OPENCODE_CONFIG` — while ranked below the project file — still contributes model ids
that the project file does not define, which is the case that matters. Setting both
closes the routing gap without a single write to the tracked tree.

Both are set together rather than picking one. `Dockerfile.runner` pins
`OPENCODE_VERSION=latest`, so the runtime version is not knowable at design time; the
two variables degrade differently on an older build, and setting both costs one small
gitignored file write instead of version probing.

The overlay is injected in `scripts/bench_run.py`, not in
`executors.build_run_environment()`. `SubprocessRunExecutor.start` passes no `env=` and
inherits the worker's environment, so anything injected there would silently miss
host-mode runs. Injecting in `bench_run.py` covers both lanes from the same function
that computes the condition manifest, keeping the hash wiring local.

### Data model

A new `GatewayModel` table holds the model catalog: the bare LiteLLM id, a
human-editable label, enabled and default flags, liveness, and a reserved
`options_json` for future id remaps and per-model options. Models that disappear from
the gateway are marked stale rather than deleted, so a provider that is temporarily
down does not lose its curated label.

Staged skills become directory-shaped. A `StagedSkillFile` child table holds every
file; `data/skills_staging/<name>/` becomes a materialization that can be rebuilt from
the database at any time. This preserves the existing invariant that the database is
the source of truth, survives a wiped `data/` or a fresh clone, and is what gives the
health panel's repair action something to repair.

### Boundaries

The API service never touches the docker socket. `docker-compose.yml` mounts it on the
worker only, by ADR-0002, and this design keeps that line intact: the health panel
learns about docker and container state through a Celery control command answered by
the worker. Repair actions that need the socket are enqueued as tasks rather than
executed in the request.

Configuration writes go through a single allowlisted, atomic `.env` editor. Keys under
`HARNESS_LAB_*` are read-only and surfaced as facts: `get_settings()` is `lru_cache`d
so they only take effect on restart, and repo root, database URL and execution backend
would break the running containers if changed underneath them.

Remote skill import is the one place the lab pulls untrusted bytes from the internet
into the harness. It is two-step by default — a preview that writes nothing, then an
explicit confirm — and every fetch is constrained: HTTPS only, hosts resolving to
loopback, private, link-local or reserved addresses rejected (the API container can
reach `host.docker.internal`, the gateway and Redis), redirects capped with the address
check re-run per hop, size and file-count caps enforced while streaming, and archive
members inspected individually rather than through `extractall`.

### Authentication

Session enforcement is a middleware rather than a per-route dependency. It fails closed
for routes added later, gives one place to audit, and leaves the existing test suite —
which resolves endpoints and calls them directly rather than going through a test
client — working untouched.

An empty password hash puts the middleware in pass-through mode, preserving today's
development flow; the user opts in explicitly and the UI shows a persistent banner
while authentication is off. The cookie is `HttpOnly` with `SameSite=Lax` and `Secure`
defaulting to false, because over plain HTTP a `Secure` cookie is silently dropped.
CSRF protection is the `SameSite` attribute plus an `Origin` host check on mutating
methods; a single-user LAN application does not need token plumbing.

The plaintext `local_username` / `local_password` pair is removed rather than kept as a
fallback. An `admin`/`admin` default behind a password-protected panel is a backdoor.

## Delivery

Seven phases (0 through 6) and seven architecture decision records (0013 through
0019), grouped into three independently useful milestones:

| Milestone | Phases | Outcome |
|---|---|---|
| M1 | 0, 1, 3 | A new LiteLLM model works end to end from the browser |
| M2 | 2, 6 | The lab runs permanently on the server, behind a password |
| M3 | 4, 5 | Skills import from a URL, drift is visible and repairable |

Phase 0 unblocks the rest: `jinja2` sits in the dev dependency group while
`Dockerfile.api` installs with `--no-dev`, so the existing Promote button fails with
`ModuleNotFoundError` in the API container, and the same import chain is what the drift
check needs.

Phases 3 and 4 each change `condition_hash` and belong in separate commits with an
architecture decision record apiece.

## Consequences

**Historical arms are re-binned by Phase 4.** Hashing a staged skill's whole directory
instead of its `SKILL.md` changes the `condition_hash` of every historical staged-skill
arm. This is unavoidable if the hash is to cover all files. Run metadata records the
hashing algorithm so old and new runs are legibly different rather than silently
incomparable.

**The gateway overlay enters the condition identity.** It is included only when
non-empty, following the rule already documented in `provenance.py`: adding keys
unconditionally would re-bin every existing arm. Today the overlay carries only
cosmetic labels, which do not change an arm; it is hashed anyway so the identity is
already correct when `options_json` starts carrying id remaps and per-model options.

**ADR-0012 is partly superseded.** `opencode.json` remains a valid routing source and
`make sync-gateway-models` remains available for making an overlay permanent, but it is
no longer required for a model to run.

**The threat model is explicit and narrow.** The panel edits credentials, fetches
remote code and reaches a worker that holds the docker socket. It is designed for a
trusted LAN or a VPN, bound to loopback by default, and must not be port-forwarded
without TLS in front.

## Validation

The load-bearing assumption is opencode's configuration precedence, which this
repository has never exercised — neither environment variable appears anywhere in the
tree today. It is confirmed in the official documentation but must be verified
empirically against the pinned runner image before Phase 3 merges: a run using a
synthetic model id absent from `opencode.json` must resolve and complete.

The acceptance criterion for the design as a whole is narrow enough to automate: after
a full configure-and-run cycle driven entirely from the browser, `git status
--porcelain` is empty, and the `condition_hash` of a pre-existing arm is byte-identical
to its historical value.

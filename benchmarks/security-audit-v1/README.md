# security-audit-v1 — audit a FastAPI service and prove each weakness

**Hidden from the agent** (`agent_hidden`): this file, `scoring.md`, `grader/` (key,
grader, fixed copy of the service) and `candidate-skill/`.

The workspace starts as `clinic-api` (`starter/`, ADR-0068), a small FastAPI service
whose functional tests pass. The agent writes `findings.json` and proof tests under
`security_tests/`; it must not touch the service. Everything runs in-process with
`TestClient`: no ports, no network.

## What it measures

Defensive API security analysis: locating weaknesses (recall against a key, precision,
decoys) and proving them (a test that fails on the vulnerable service and passes on a
fixed one). The audit half of the security lane; `security-fix-v1` is the fix half. The
case the candidate skill `audit_api_security` is trialled on (ADR-0087).

## Grading

`grader/grade.py check <name> <workspace> <run_id>`. Proofs copy the service (starter or
`grader/fixed/`) and the agent's `security_tests/` into a scratch dir and run one pytest
node there, with `PYTHONPATH` = scratch first, so a test that imports from an absolute
path or reads the workspace proves nothing. Pytest exit 1 on the starter and 0 on the
fixed copy is a proof; anything else (collection error, timeout, a test that passes on
both) is not.

TestClient needs `httpx2` (starlette 1.3). It is declared in `validation_requirements`
for the grading lane and added to `Dockerfile.runner` so the agent can run its own
tests; until the runner image is rebuilt, the agent cannot import `fastapi.testclient`.

## Changing the case

A new weakness or decoy is a new instrument (ADR-0073): bump `version`, update
`grader/fixed/`, `key.json` line windows and the calibration under
`data/reference/security-audit-v1/`. Never describe the key outside the hidden paths.

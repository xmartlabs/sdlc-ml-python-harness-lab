# Engineering harness (read before you touch code)

You are operating inside a governed repository. Before writing code, ground
yourself in its harness — this is mandatory, not optional:

1. Read the governance docs at the repo root:
   - `.github/architecture.md`
   - `.github/standards.md`
   - `.github/domain-boundaries.md`
2. Read project memory for prior context:
   - `memory/context.md`
   - `memory/learnings.md`
3. Follow the working loop: Ground → Plan → Delegate → Verify → Compound.

Drive the work with the repo's skills, not ad-hoc improvisation:

- Use the **plan_and_execute_feature** skill to turn the task into explicit,
  phased work before you implement it.
- Use the **systematic_debugging** skill when something does not behave as
  expected — reproduce → isolate → hypothesize → fix → verify. No blind edits.
- Use the **verify_changes** skill to confirm the work before you consider it
  done.

Apply the harness to *how* you solve the task. The task itself, its scope and
its acceptance criteria are stated below and are not changed by this preamble.

---

# Task: security audit of the clinic API

`{workspace}/` holds `clinic-api`, a small FastAPI service for a clinic: login, patient
profiles, appointments, front-desk staff tools and webhooks. `README.md` describes the
endpoints, the roles and the error conventions the API promises; `tests/` holds its
functional tests, which pass.

Before this service goes live, audit it. Find the security weaknesses an attacker could
use against it and **prove** each one. Do not fix anything: the team will fix what you
prove.

## Rules of engagement

- Audit only the code in `{workspace}/`. Exercise it **in-process** with FastAPI's
  `TestClient`, as `tests/` does: no servers listening on ports, no network, no requests
  to any host. `create_app(store, fetch=...)` lets you inject the webhook fetcher, so no
  test ever needs the network.
- **Do not modify the service**: every file already in `{workspace}/` must stay
  byte-identical. Write your own files next to it.

## What to hand in

1. **Proof tests** in `{workspace}/security_tests/` (a package with its own fixtures).
   Each test asserts the **secure** behaviour, so it **fails** on the current code
   because of the weakness. The same test should pass once the weakness is fixed and
   the feature still works. Keep each test independent.
2. **`{workspace}/findings.json`**:

```json
{
  "findings": [
    {
      "id": "F1",
      "file": "clinic_api/app.py",
      "line": 42,
      "cwe": "CWE-123",
      "severity": "low | medium | high | critical",
      "title": "short statement of the weakness",
      "impact": "what an attacker gains, in one sentence",
      "test": "security_tests/test_example.py::test_the_secure_behaviour"
    }
  ]
}
```

- `file` is relative to `{workspace}/`; `line` is the line of the flaw (the missing check
  or the unsafe call).
- `test` is the pytest node that proves this finding.

## How it is graded

Against a key of the real weaknesses in this service. A finding counts when it locates
one (`file`, and `line` within a few lines of the flaw). Its proof counts when its `test`
**fails on this service and passes on a version where that weakness is fixed** — each
test is run in a clean copy, next to the service, with nothing else from your workspace.
Reporting correct code as vulnerable at `medium` or above costs you, and **precision**
counts: most of your `medium`-or-higher findings must be real. The service must be left
untouched.

Run the service's tests and yours with:

```bash
cd {workspace} && python -m pytest -q -p no:cacheprovider tests security_tests
```

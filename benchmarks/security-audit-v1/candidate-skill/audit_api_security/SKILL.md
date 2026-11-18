---
name: audit_api_security
description: Use when asked for a security audit or security review of an API or web service — "security audit", "API security review", "find vulnerabilities", "OWASP API", "is this endpoint safe". Maps the endpoints and the auth model, walks the OWASP API Top 10 classes, traces untrusted input to sinks, proves each finding with a failing test before reporting it, and reports CWE, severity and impact. Defensive and scoped to the code provided; not for attacking live systems.
---

# Skill: audit_api_security

## Purpose

Find the weaknesses in an API that an attacker could use, and prove each one, so the
team can fix the real problems first. An audit is judged on what it finds, on how sure
each finding is (a proof beats a hunch) and on what it does not waste time on (false
positives).

## Scope and rules of engagement

- Work only on the code and the environment you were given. Never send requests to a
  host, service or address outside it, even one that the code mentions.
- Exercise the service **in-process** (a test client, a fake transport, an injected
  fetcher). Do not start servers that listen on ports or make real network calls.
- Do not modify the code under audit unless asked; write your proofs as separate tests.
- If you come across secrets or credentials, note them as a finding. Do not use them.

## Workflow

1. **Map the surface.** List every endpoint: method, path, who may call it (anonymous,
   any user, a role), what it reads and writes. Read the auth code end to end: how
   credentials are checked, how a session or token is issued and verified, and where the
   caller's identity and role come from on each request. Read the documented rules
   (README, docstrings, error conventions); a weakness is often a gap between a rule and
   the code.
2. **Walk the classes, one pass each.** The OWASP API Security Top 10 is a good checklist:
   - object-level authorization: can a user reach another user's object by its id?
   - authentication: can a credential or token be forged, replayed or skipped?
   - object property level: can a request set fields it should not (role, owner, price)
     or read fields it should not see?
   - unrestricted resource consumption: are there unbounded loops, sizes or retries?
   - function-level authorization: can a lower role call a privileged function?
   - sensitive business flows: can a flow be abused at scale (enumeration, brute force)?
   - server-side request forgery: does the server fetch a URL the caller controls?
   - security misconfiguration: debug modes, permissive defaults, verbose errors.
   - inventory: forgotten or undocumented endpoints.
   - unsafe consumption of other APIs: trusting what an upstream returns.
   Add injection (SQL, command, template, path) for every place input reaches an
   interpreter or the filesystem.
3. **Trace, don't pattern-match.** For each candidate, follow an attacker-controlled value
   from the request to the sink. Code that *looks* dangerous is often guarded upstream
   (escaped input, a role check, a deliberate design). Read the guard before you report.
4. **Prove it.** Write a test that asserts the **secure** behaviour and run it: it must
   fail on the current code. That failure is your evidence. Keep each test focused on a
   single weakness and make it independent of the others (its own fixtures, its own
   data). A good proof would pass once the weakness is fixed and the feature still works,
   so assert on what an attacker gains, not on an incidental detail like an exact error
   string.
5. **Rate it.** Severity reflects what an attacker gains and how easily:
   - `critical`: account takeover, privilege escalation, data of every user;
   - `high`: another user's private data, or reaching internal systems;
   - `medium`: information that helps an attack (enumeration), limited exposure;
   - `low`: hardening with no direct exploit.
   Name the closest CWE.
6. **Report.** One finding per weakness, located at the line where the flaw is (the
   missing check, the unsafe call), with the impact in one sentence and the test that
   proves it. Use the requester's format when one is given.

## Checklist before you hand it in

- [ ] Every finding has a test that fails on the current code for the stated reason.
- [ ] Every test exercises the service in-process; no real network, no external hosts.
- [ ] Nothing reported is guarded elsewhere or documented as intended behaviour.
- [ ] Each finding points at the flawed line, with CWE, severity and impact.
- [ ] The code under audit is unchanged.

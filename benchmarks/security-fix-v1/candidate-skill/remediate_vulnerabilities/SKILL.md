---
name: remediate_vulnerabilities
description: Use when asked to fix security vulnerabilities, act on pentest or security-audit findings, or harden existing code — find where untrusted input reaches a dangerous sink, prove each weakness with a failing exploit test, fix it at the root without breaking the feature, and report each fix with its CWE.
---

# Skill: remediate_vulnerabilities

## Purpose

Close security weaknesses in existing code so that **no** input reaches the unsafe
behaviour, while every legitimate use keeps working. A fix that blocks the one payload
you tried, or that deletes the feature, is not a fix.

## Required Input

- The code to harden (a repository, a module, a service).
- Whatever is known about the findings: a pentest report, a scanner's output, or only
  "there are issues". Treat a vague report as "audit everything reachable".
- How to run the existing tests.

## Output Format

- The fixed code, with the public API unchanged unless the task allows otherwise.
- One exploit test per weakness: it fails before the fix and passes after it.
- The existing tests, still passing.
- A findings report, in the file the task names (`SECURITY.md` by default), with one
  entry per weakness: location, CWE id (`CWE-89`), impact in one line, and the fix.

## Execution Rules

1. **Inventory the attack surface first.** List every entry point a caller controls
   (public functions, handlers, CLI arguments, files read, blobs imported) and every
   dangerous sink:
   - SQL strings, including `LIKE` patterns;
   - file paths and archive member names;
   - `subprocess` and `os.system`;
   - deserializers (`pickle`, `yaml.load`, `marshal`);
   - template rendering and HTML output;
   - outbound URLs;
   - any lookup by id that returns or changes a resource.
2. **Trace sources to sinks.** For each sink, ask whether a caller-controlled value can
   reach it unvalidated. For each resource lookup, ask whether the code checks that
   *this caller* may act on *this resource*. A missing ownership check is a
   vulnerability (CWE-639/862) even when no input is malformed.
3. **Prove before fixing.** Write a failing test that performs the attack as an outside
   caller would. Cover more than one variant:
   - for traversal: `../`, an absolute path, and `....//`;
   - for injection: quotes, comment markers and `UNION`;
   - for a shell: `;`, `$(...)` and backticks.
   If you cannot make the test fail, you have not found the weakness yet.
4. **Fix the mechanism, not the input.** Prefer the construct that makes the attack
   impossible over a filter that recognises it:

   | Weakness | Fix |
   |---|---|
   | SQL injection (CWE-89) | Parameterised queries; escape `LIKE` wildcards when the input is a literal |
   | Broken access control (CWE-639/862/863) | Enforce ownership in the query or at one choke point on **every** read, write, list and delete. Answer "not found" for others' resources, so ids do not leak |
   | Path traversal (CWE-22) | Canonicalise (`resolve`/`realpath`), then require the result to be inside the allowed directory. Reject rather than silently rewrite |
   | OS command injection (CWE-78) | Use the language's library (`tarfile`, `shutil`) or an argv list without a shell. Never `shell=True` with caller data |
   | Unsafe deserialization (CWE-502) | A data-only format (JSON) with type checks. Never `pickle` on data that crossed a trust boundary |
   | Secrets and auth | Constant-time compare; a slow salted hash; tokens from a CSPRNG |

   A blocklist (`if ".." in name`) is a last resort. It is almost always bypassable.
5. **Keep the feature.** After each fix, exercise the feature with legitimate inputs:
   spaces, quotes, apostrophes, `&`, parentheses, dots, non-ASCII. Run the existing
   tests. Do not remove, disable or narrow a feature to make an attack fail. Refusals
   use the code's existing exception types.
6. **Look for siblings.** The same mistake usually appears more than once: every
   method of a class, every place a path is joined, every query built from strings.
   Fix the class of bug, ideally through one helper every caller goes through.
7. **Verify.** Run the exploit tests (now green) and the full suite. Re-read the diff
   for sinks you touched but did not test.
8. **Report.** Write one entry per weakness with its CWE. Say what you did not fix and
   why, rather than staying silent.

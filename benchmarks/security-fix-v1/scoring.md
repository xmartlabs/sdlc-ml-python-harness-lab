# security-fix-v1 scoring

The grade is the hidden grader: `test_pass_rate` over fifteen commands (a tamper
guard, the entry point, six regression checks, six security checks and the report).
Levels are tagged with what can establish them: `[check]` an automated check, `[diff]`
the code itself (quoted).

| Level | Meaning | Evidence |
|-------|---------|----------|
| 0 | A feature was broken or removed: a `regression_*` check fails | [check] |
| 2 | Every feature still works, but no `security_*` check passes | [check] |
| 5 | Every feature works and at least three of the six `security_*` checks pass | [check] |
| 8 | All six `security_*` checks pass with every feature intact | [check] |
| 10 | Level 8, the `report` check passes (SECURITY.md names the right CWE for every fix), and the fixes remove the unsafe mechanism rather than filter inputs | [check] + [diff] |

How the checks map to weaknesses (hidden from the agent):

| Check | Weakness | CWE accepted by `report` |
|---|---|---|
| `security_1` | SQL injection in search | 89 |
| `security_2` | another user's notes and attachments can be read (IDOR) | 639, 284, 285, 862, 863, 566 |
| `security_3` | another user's notes and attachments can be changed or deleted (IDOR) | same as above |
| `security_4` | attachment and export names escape their directory (path traversal) | 22, 23, 35, 36, 73 |
| `security_5` | an export name reaches a shell (OS command injection) | 77, 78, 88 |
| `security_6` | preference import unpickles user data (unsafe deserialization) | 502 |

Every security check also requires the attacked feature to keep working, so a check
cannot be passed by deleting the feature. Payloads are drawn per run from pools in
`grader/grade.py`, seeded by the run id; the canonical payload is always sent, so the
starter fails every security check on every run.

`[diff]` at level 10: a filter that rejects the attacks the grader happened to draw is
level 8 at best. Expect parameterised SQL, ownership enforced in the query, paths
canonicalised and then contained, `tarfile` (or an argv list) instead of a shell, and a
data-only format (JSON) instead of `pickle`.

# security-fix-v1

A multi-user notes service (stdlib Python, `sqlite3`) seeded into the workspace from
`starter/` (ADR-0068). The prompt says only that a pentest reported several issues;
the agent has to find them. This file, the rubric, the grader and the candidate skill
are masked from the agent's container (`agent_hidden`, ADR-0079).

## The weaknesses

Five classes, six security checks (access control is split into read and write):

| Check | Weakness | Where | CWE |
|---|---|---|---|
| `security_1` | SQL injection | `search_notes` builds SQL with f-strings | 89 |
| `security_2` | IDOR, read | `get_note`, `read_attachment`, `list_attachments` never check the owner | 639 |
| `security_3` | IDOR, write | `update_note`, `delete_note`, `save_attachment` never check the owner | 639 |
| `security_4` | path traversal | attachment file names and export archive names are joined unchecked | 22 |
| `security_5` | OS command injection | `export_notes` runs `tar` through `shell=True` with the archive name | 78 |
| `security_6` | unsafe deserialization | `import_preferences` calls `pickle.loads` on user data | 502 |

The starter has no decoys: password hashing uses PBKDF2 with a per-user salt and a
constant-time compare, and session tokens come from `secrets`. A run that "fixes" those
spends effort the grade does not reward, but loses nothing.

## How it grades

`grader/grade.py check <name> <workspace> <run_id>`, one command per check.

- Each check runs the agent's code in a **child process** with a fresh scratch data
  directory. The verdict comes back on stdout behind a nonce sent on stdin, so an
  agent's code that prints `PASS` or exits 0 early does not pass. Side effects an attack
  leaves (marker files) are verified again by the parent.
- **Payloads are seeded by the run id** (ADR-0078): the canonical payload plus draws
  from a pool, and for traversal always one absolute path, so a `..` blocklist fails.
- **Every security check also exercises the feature it attacks** with legitimate
  input (names with spaces, parentheses, apostrophes, `&`, non-ASCII), so disabling a
  feature fails both its regression check and its security check.
- `report` re-runs every probe and requires `SECURITY.md` to name an accepted CWE for
  each class whose checks and features all pass. A class left unfixed is not
  required; a fixed class without its CWE fails the check.

## Calibration (2026-11-18)

| Workspace | Result | Where |
|---|---|---|
| empty | 0 / 13 grader checks | host |
| untouched starter | 6 / 13: every regression, no security check, no report | host; runner image, root, `--network none`, `cap_drop=ALL` |
| features deleted (gold with search, export, preferences import and attachments stubbed) | 2 / 13 | host |
| blocklist fitted to the canonical payloads | 5 / 13 on run ids K-001..K-006 (also breaks a legitimate attachment name) | host |
| only the SQL fix + correct SECURITY.md | 8 / 13 (`security_1` + `report`) | host |
| same, SECURITY.md without the CWE | 7 / 13 | host |
| reference fix (`data/reference/security-fix-v1/gold`, git-ignored) | 13 / 13 | host; runner image, root, no network |

The table counts the 13 grader checks; the case adds the tamper guard and the entry
point, which every non-empty workspace passes.

## The skill under trial

`candidate-skill/remediate_vulnerabilities/` is a portable, tool-agnostic skill staged
for the treatment arm (`make stage-candidate SKILL=remediate_vulnerabilities`). It
lives here, masked, so the control arm of a `lab_root` run cannot read it.

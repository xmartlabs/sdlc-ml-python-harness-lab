# Proposal workflow (evidence → sanitized issue)

Phase 3 of the harness continuous-improvement cycle turns a run's behavioral audit
into a durable, schema-validated, **versioned** proposal and a sanitized public
issue. Reviewed proposals are the source of truth; they live in a tracked tree,
separate from the git-ignored run data.

## Where proposals live

```
data/harness-proposals/<year>/HEP-<year>-NNN.yaml
```

Unlike `data/runs/` (git-ignored), this tree is **versioned** — it is the reviewed
record. IDs are `HEP-YYYY-NNN`, allocated per year. The YAML schema is the Pydantic
model in `src/harness_lab_app/harness_lifecycle/models.py`.

## Commands

| Target | Purpose |
|---|---|
| `make harness-proposal RUN=<id> [IMPROVE_MODEL=…] [DRY_RUN=1]` | Draft a proposal from the run's audit; print a sanitized issue preview. |
| `make harness-proposal-issue PROPOSAL=HEP-YYYY-NNN [CREATE=1] [DRY_RUN=1]` | Preview or file the public issue in `ml-python-base`, idempotently. |

`DRY_RUN=1` writes nothing to git or the filesystem. Issue creation requires an
authenticated `gh` and only runs with `CREATE=1`.

## End-to-end

1. Produce a run and its audit (`make bench-audit RUN=<id>`).
2. `make harness-proposal RUN=<id>` — synthesizes a draft, allocates the next
   `HEP-<year>-NNN`, extracts run evidence (condition hash + `metadata.json`
   checksum), writes the YAML, and prints the sanitized issue preview.
3. **Review the YAML**: wording, target artifacts under `.github/…`, validation
   criteria, and that the public preview leaks nothing.
4. `make harness-proposal-issue PROPOSAL=<id> CREATE=1` — files the sanitized issue
   in `ml-python-base` (labels `lab-proposal`, `validation-required`), then records
   `links.source_issue` and appends an `issue_created` lifecycle event.
5. Implement the change as a small `ml-python-base` PR linked to the proposal and
   issue, then cut a SemVer release (see the template's release lifecycle).

## Guarantees

- **Stable identity & lifecycle.** Every proposal carries `schema_version`, a
  `HEP-YYYY-NNN` id, and an append-only `lifecycle` (`observed` → `proposed` →
  `issue_created`).
- **Evidence integrity.** Each source run records its `condition_hash` and the
  SHA-256 of its `metadata.json`.
- **Idempotent issue linkage.** Once `links.source_issue` is set, re-filing is a
  no-op — public history is never silently rewritten.
- **Sanitized public output.** The issue is built from an explicit field allowlist
  (private run IDs and artifact paths are never surfaced — only aggregate condition
  hashes), then scanned for tokens, key material, home paths, internal endpoints,
  `.env` assignments, and private-repository links. Any hit blocks publication.

## Relationship to `make improve`

Both paths are supported; they answer different needs. `make improve RUN=<id>`
(`scripts/lab_improve.py`, and the **Propose improvements** → **Save** flow on the
run detail) is the light, everyday path: it synthesizes proposals into
`improvements.json`, feeds the cross-run **Improvements** worklist, and files issues
only when you pass `CREATE=1` — see [improvement-cycle.md](../improvement-cycle.md).
`make harness-proposal` is the rigorous HEP form for a proposal that will cross into
the template: stable IDs, schema validation, evidence checksums, lifecycle, and
sanitization.

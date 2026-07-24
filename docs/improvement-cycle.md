# Continuous-improvement cycle — lab → GitHub issues

The lab is a feedback loop for your engineering harness: run cases against a
harness/model, let the LLM audit judge how well the skills, governance docs, and
subagents served the run, then turn the "needs improvement" signals into
**GitHub issues** in the repos that own the harness. You stay in control — issues
are only filed when you run the create step.

Targets (override with `IMPROVE_REPO_TEMPLATE` / `IMPROVE_REPO_HARNESS`):

- **`ml-python-base`** — skills, governance docs, subagents, hooks (the template).
- **`harness-engineering-guide`** — methodology / process / cross-cutting docs.

## The loop

```
run a case  →  make bench-audit RUN=<id>   (or the "Run audit" button)
            →  make improve RUN=<id>        # synthesize proposals (dry-run)
            →  review/edit improvements.json
            →  make improve RUN=<id> CREATE=1   # file the issues (your approval)
```

### 1. Audit the run

`make improve` needs the run's audit. Produce it with
`make bench-audit RUN=<id>` or the **Run audit** button on the run detail. The
audit writes `audit.md` + `audit_findings.json` (per-item verdicts).

### 2. Generate proposals (dry-run, default)

```bash
make improve RUN=tetris-v1-K-004
```

A dedicated LLM pass reads the audit + findings + run context and synthesizes at
most ~6 concrete, evidence-grounded proposals. They're written to:

- `data/runs/<run>/improvements.json` — the machine record (edit this).
- `data/runs/<run>/improvements.md` — a readable preview.

Each proposal has a `title`, `body` (problem + proposed change + why, grounded in
this run's evidence), `kind`, `target`, and a routed `repo`.

### 3. Review and edit

Open `improvements.json` and curate: **change `repo`** to re-route, edit titles
or bodies, or delete proposals you don't want. This file is the source of truth
for the next step. (Routing is a fixed default by kind/name — this is where you
override it.)

### 4. File the issues (your approval)

```bash
make improve RUN=tetris-v1-K-004 CREATE=1
```

Files each remaining proposal as a GitHub issue via `gh` (on your host, with your
auth), labeled `lab-improvement`, with a footer linking back to the run
(harness / model / case). Created issue URLs are written back into
`improvements.json`, so re-running `CREATE=1` is idempotent (won't duplicate).

## Cross-run worklist: saved proposals → one combined sanitized issue

The per-run flow above ends 1:1 (one run → its proposals → its issues). When you
iterate the same case across template versions, the **Improvements** page in the
web UI lets you group evidence from several runs into a single issue:

```
run detail  →  "Propose improvements"  →  "Guardar"        # snapshot to the DB
Improvements page  →  select N proposals (any runs/cases)  →
"Generar issue combinado (N)"  →  ONE sanitized ml-python-base issue
```

1. **Save** (`POST /api/runs/{id}/improvements/save`): snapshots each proposal
   from `improvements.json` into the `savedproposal` SQLite table, one row per
   proposal, keyed by `(run_id, title)` so re-saving updates instead of
   duplicating. The row also captures run context (case, harness, executor
   model), the **run date** (`metadata.json` `created_at`) and the **template
   version** — the adopted release ref (`v0.3.0`) from `.harness-version.yaml`
   when present, else the run's short `lab_git_commit` — plus the exact
   `harness_fingerprint.harness_hash`. Date + version are the columns you scan
   to compare iterations of the same case across template releases.
2. **Group** (`Improvements` page, `GET /api/improvements`): table of all saved
   proposals with checkbox multi-select and repo/kind/case filters.
3. **Combine** (`POST /api/improvements/group-issue`): one LLM call
   (`drafting.synthesize_group_issue`) consolidates the selected proposals —
   plus each run's `audit.md` excerpt — into ONE governed proposal, rendered
   through the same sanitizer + issue-form pipeline as the per-run
   `GET /api/runs/{id}/proposal-issue`. The `source_runs` field lists every
   run's condition hash. Selected rows flip to `issued` and the result is
   recorded in the `groupissue` table.

**Sanitization:** saved proposal bodies are *not* sanitized (they routinely
mention run ids and local paths), so the combined pipeline never embeds them
verbatim: content is regenerated via the strict-JSON draft (allowlist render),
the group system prompt bans private identifiers, and a deterministic backstop
rejects (HTTP 422) any output echoing a selected run id.

The per-run "Issue sanitizado" card on the run detail is unchanged; the
worklist is an additive path for multi-run grouping.

## Notes

- **Approval is the create step.** Dry-run generates and previews only; nothing
  is filed until you run `CREATE=1`. Edit the JSON first.
- **Requirements:** `gh` authenticated (`gh auth status`), the gateway reachable
  (`GATEWAY_*` in `.env`) for the synthesis pass. Runs on the host — pair it with
  the host or docker mode you're already using.
- **Model:** the synthesis uses the auditor model by default (`gpt-5.4-mini`);
  override with `IMPROVE_MODEL=<litellm model name>` (e.g. `gpt-5.5` for a
  stronger pass).
- **Any harness:** works for opencode, codex, and claude runs — whatever produced
  the audit.

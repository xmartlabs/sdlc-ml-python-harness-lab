# ADR-0005: Structured harness attribution from run logs

- **Status:** Accepted
- **Date:** 2026-07-07
- **Deciders:** Marcos Soto (with Claude Code)
- **Related:** ADR-0003 (provenance/fingerprint), ADR-0004 (validation/score),
  `src/harness_lab_app/attribution.py`, `docs/roadmap-local-webapp.md`

## Context

Provenance (ADR-0003) records which harness was *available* for a trial (skills,
agents, governance docs — with hashes). To evaluate the harness we also need to
know which of it the agent *actually exercised*: did it read the governance
docs, which tools did it use, did it plan? The existing `bench-audit` answers
this qualitatively via an LLM; we need a machine-readable, aggregable version so
it can feed ablation comparisons.

The opencode step logs (`data/runs/<id>/steps/step-*.log`) contain observable
signals in a stable `key=value` format: `permission=<read|edit|bash> pattern=…`,
`"shell tool using shell"`, `agent=<mode>`. They do **not** contain explicit
skill-invocation events — opencode injects skills into the model context rather
than reading each `SKILL.md` as a file.

## Decision

We will parse the run's step logs into a structured `Attribution` record
(`attribution.py` pure parser + `AttributionService` + `Attribution` table):

- **tools** — counts of read / edit / bash / shell.
- **files** — distinct files read and edited (edited comes only from
  `permission=edit/write`; `"touching file"` is the file-watcher and is *not*
  treated as an edit).
- **harness_usage** — coverage of the harness "reading surface" (governance docs
  + project memory) that the agent actually read; skills available (from
  `metadata.json`) vs consulted. This "used vs available" cross-reference is the
  core attribution signal.
- **bash_commands** histogram and **agent_modes** histogram.

Attribution is pure log parsing (no container), so it runs inline in the API
(`POST /api/runs/{id}/attribution/index`, `GET …/attribution`) and is indexed
automatically after every run — including cancelled/failed ones, since a
partial run's behavior is still informative.

**Honesty about skills:** skill *invocation* is only weakly observable (a skill
is counted "consulted" solely if its `SKILL.md` appears in the read log — a
lower bound). We record this limitation in the data rather than fabricating a
number; deeper skill attribution needs the harness to emit invocation events
(future work).

## Consequences

- Easier: each trial carries a comparable, quantified record of "harness
  applied" — e.g. K-001 read 5/8 governance docs, maintained a `PLAN.md`, ran
  `python` 16× to self-test. This becomes a per-arm feature for pillar 4.
- The parser is coupled to opencode's log format; if that changes, the regexes
  must follow (they are centralized and unit-tested against real samples).
- Skill-usage coverage reads 0/N until the harness emits invocation events; the
  `skills_note` field makes that explicit so it is not misread as "no skills
  used."

## Alternatives considered

- **Rely only on `bench-audit`** (LLM reading the logs) — rejected: qualitative,
  not aggregable, and costs a model call per run.
- **Parse the opencode usage SQLite DB** — rejected for now: it holds token
  usage, not tool/file/skill events; the step logs are the richer source.
- **Treat `"touching file"` as edits** — rejected after it wrongly flagged
  read-only governance docs as edited; edits come only from `permission=edit`.

# ADR-0065: The audit decides what code can decide

- **Status:** Accepted
- **Date:** 2026-10-21
- **Deciders:** Marcos Soto
- **Related:** [ADR-0048](0048-a-check-that-did-not-measure-is-not-a-failure.md),
  [ADR-0054](0054-a-repository-is-audited-before-the-lab-runs-anything-on-it.md),
  [ADR-0058](0058-the-audit-maps-harness-components-to-claims.md),
  [docs/plans/2026-10-harness-ecosystem-analysis-and-improvement-plan.md](../plans/2026-10-harness-ecosystem-analysis-and-improvement-plan.md)
  (finding 3.4, Phase 4)

## Context

The repository audit (ADR-0054/0058) is two model passes over truncated evidence. It
describes a harness well and decides things about it badly. The three audits on record
proposed only "Comprobar que…" cases — reading checks — and none of them noticed what a
person finds in a minute by reading two files:

- `ml-python-base`'s `make test` ends in `pytest … || echo "No tests found"`, so a
  failing suite exits 0, while eight files tell agents to verify with it;
- its Stop hook prints a reminder and exits 0, which Claude Code shows to the user and
  never to the model, so it enforces nothing;
- its CLAUDE.md is 206 lines; its MCP server is on `@latest`; no harness version is
  declared.

These are facts with a right answer. A model asked to describe a repository may or may
not mention them, may phrase them differently every run, and cannot be held to them.

## Decision

**A fixed set of deterministic checks runs inside every audit, before the model.**
`harness_checks.run_checks(paths, read)` takes the same inputs as `collect_evidence`
and returns one row per check — `id`, `title`, `status` (`pass` | `warn` | `fail` |
`not_applicable`), the evidence it saw, why it matters, and the fix:

| Check | Fails / warns when |
|---|---|
| Verification can fail | a `test`/`check`/`ci`/`verify` target swallows failures (`\|\| echo`, `\|\| true`, `\|\| exit 0`); warns when there is no such target |
| Verification does not rewrite the tree | the target runs a formatter or auto-fixer |
| Hooks can enforce, not only remind | every command hook only prints and exits 0 (no `decision` object, no `exit 2`) |
| Always-loaded instructions stay a map | no rules file (fail), or one over 200 lines / ~2,500 tokens (warn) |
| Skills are discoverable | a skill has no `name`/`description` (fail), or a name outside lowercase-hyphen (warn) |
| MCP servers are pinned | a `.mcp.json` package is `@latest` or unversioned |
| CI runs the same gate the agent runs | no workflow, or none calls a local verify target |
| The harness version is declared | no `.harness-version.yaml` / `.template-version` |

Rules that follow from it:

1. **Absence is `not_applicable`, never `pass`** (ADR-0048). A repository with no hooks
   has not passed the hook check.
2. **The report prints the checks itself**, in a section of their own after the summary,
   whether or not the model's answer parsed. The model is shown them in the evidence,
   and the case pass is given the flagged ones, so proposals start from a measured gap.
3. **Skills are counted once, at their source.** Governed templates project each skill
   into several tool folders as symlinks; read through git, a symlink is its target path,
   so projections are skipped rather than reported as skills without frontmatter.
4. **The same checks run outside the stack:** `make harness-check REPO=<path>` reads a
   local repository's committed tree and exits 1 on a failure, so a team can run it on
   their own project in seconds or gate CI with it.

## Consequences

- An audit now says the same thing about the same tree every time, for the claims that
  have a right answer, and the model's reading is confined to what needs judgement.
- Run on `ml-python-base` v1.0.0 the checks report one failure (`make test` swallows
  failures) and five warnings; on this lab, the same — it carries the template's v0.2
  harness. Those are real defects, which is the point.
- The checks read text, so a hook that blocks through an indirection the regex cannot
  see (a script that calls another script that exits 2) is reported as advisory. The
  evidence names the script, so a reader can overrule it.
- Executing hooks with synthetic events in an isolated copy is the next step; it runs
  target code and belongs in the hardened lane, which this ADR does not change.

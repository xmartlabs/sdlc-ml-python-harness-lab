# ADR-0072: A developer takes the harness home, and the docs follow the journey

- **Status:** Accepted
- **Date:** 2026-10-25
- **Deciders:** Marcos Soto
- **Related:** [ADR-0031](0031-harness-source-is-a-run-condition.md),
  [ADR-0065](0065-the-audit-decides-what-code-can-decide.md),
  [ADR-0070](0070-the-interface-speaks-english-and-spanish.md),
  [ADR-0071](0071-a-grader-brings-its-own-dependencies-and-never-the-agents-tests.md)

## Context

The lab is meant to be used by developers who did not build it: to try a task they
vibecoded, compare models on it, audit their own repository's harness, measure a
skill, and keep whatever helped. A review of the code and the documentation on
2026-10-25 found that it could do most of that and explained almost none of it:

- **There was no way to take anything home.** Promoting a skill wrote it into the
  lab. The projection that puts the lab's harness into a repository existed — it is
  the `injected` experiment arm — but only inside a run's workspace.
- **The documentation had no path through it.** About 7,900 lines across 34 guides,
  plus a 596-line README that opened with run provenance and Celery. Two overlapping
  documentation maps, no index. The harness/credential matrix was written four
  times, the audit wizard three, the improvement loop five. The onboarding's smoke
  test named a case and a prompt variant that no longer existed, and contradicted
  the adapter guide about whether claude goes through the gateway.
- **The app sent people nowhere.** Every page's "Open the guide" opened the demo
  guide; the "New to the lab?" panel could never appear, because benchmark cases are
  seeded on startup; Cases, Audit and the run page had no help at all.
- **Two forms were broken for a newcomer.** *Validate your tool* saved a drafted case
  with an empty rubric, which the backend refuses — the step could not be completed.
  The workbench preset *native vs native + skills* offered the lab's governed skills
  on an arm that, being `native`, does not have them, and asked the agent to invoke
  them.
- **One guard assumed one developer's setup.** The claude preflight added on
  2026-10-23 refused any Claude model the gateway lacked a card for. That holds only
  when the claude CLI is routed through the gateway, which is set in an untracked,
  per-developer `.claude/settings.local.json`. For anyone else, it would have refused
  models Anthropic serves.

## Decision

1. **`make harness-export`** projects the lab's harness into a repository the
   developer owns, with the same code the `injected` arm uses. Three sizes: one
   skill (`SKILLS=… GOVERNANCE=0`, the skill file and nothing else), some skills with
   the rules, or the whole harness. It **previews by default** — it projects into a
   throwaway copy and reports every file it would add or change — and writes only
   with `APPLY=1`. It refuses a repository with uncommitted work, keeps any file it
   replaces as `*.native.bak`, and never writes into the lab. `LIST=1` prints the
   skill names.
2. **The documentation is organised by journey.** `docs/README.md` is the index:
   what a developer gets, and six one-page journeys — create a case, run and
   compare, read the results, bring your repo or skill, take it home, improve the
   template. Each ends by pointing at the next. The existing guides stay as
   reference and are linked, not repeated; design and history documents are listed
   apart and carry a banner. The root README is rewritten around what a developer
   gets, in a quarter of its length. A test holds every link in the entry documents
   to a file and heading that exist.
3. **The app points at the journeys.** The in-app Guide opens on the index, lists
   the six journeys first and opens a link between two catalogued documents in
   place. "Open the guide" on a page opens that page's journey. Cases, Audit and the
   run page gain help. The new-to-the-lab panel shows until the first validated run.
4. **The two broken forms are fixed.** *Validate your tool* keeps the drafted rubric
   and falls back to a plain one; the *native + skills* preset lists staged skills
   only — the ones `skills_extra` adds to a `native` arm.
5. **A Claude model is held to the gateway only when claude uses it.** The preflight
   and the native-model catalog check gateway coverage only when the lab's Claude
   settings set `ANTHROPIC_BASE_URL`; otherwise the models are not marked and not
   refused.

## Consequences

- A developer can carry one skill, measured in the lab, into a project in two
  commands, see exactly what changes first, and undo it with git.
- What is exported is what was measured — the same projection, not a second copy of
  the logic to drift.
- Guides that duplicate each other still exist; the journeys now say which to read,
  and the duplicates can be folded in over time without breaking anyone's link.
- The export does not merge a skill into an existing one of the same name or
  rewrite a project's own rules — it replaces the file and keeps the original as
  `*.native.bak`. A project with substantial governance of its own should export
  skills only.

# ADR-0049: A bare arm is a masked runner, not a lab-root run

- **Status:** Accepted
- **Date:** 2026-09-08
- **Deciders:** Marcos Soto
- **Related:** [ADR-0029](0029-target-repositories-pinned-per-run.md),
  [ADR-0031](0031-harness-source-is-a-run-condition.md),
  [ADR-0041](0041-a-skill-under-test-is-invoked-not-just-available.md),
  [ADR-0044](0044-a-single-prompt-case-can-carry-the-full-harness-pair.md),
  [ADR-0046](0046-an-experiment-reports-validity-before-it-reports-a-verdict.md),
  [ADR-0047](0047-a-case-exposes-two-prompt-variants-the-task-and-the-task-plus-the-harness.md)

## Context

This lab exists to answer whether the harness earns its keep — the governance
body, the skills, the hooks, the subagents, the prompt preamble. Its base case is
Tetris. Until now that case **could not be run against nothing**: every lab-venue
run launches the agent from the lab root, so the "bare" arm of a full-harness
experiment carried CLAUDE.md, the SessionStart hook, `.claude/agents`,
`.claude/commands`, `.github/` and `memory/` exactly like the treatment. The only
lever that removed a harness was `harness_source=native|injected` (ADR-0031), and
both require an external repository to be *inside*. ADR-0044 wrote that limit
down and pointed at the external-repository route; ADR-0046's wizard advisory
repeated it on every skills experiment. Both were correct, and neither gave a
lab-native case anywhere to go.

Two archived `native` runs say precisely how much a different cwd buys. Their
`system/init` events list only Claude Code's built-in agents and no lab slash
commands, and no `SessionStart` hook fired: **`.claude/` is not read from an
ancestor directory.** But both came up with the lab's `context7` and `git` MCP
servers while having no `.mcp.json` of their own — `/app/.mcp.json` was read from
the parent — and Claude Code's memory lookup collects `CLAUDE.md` from every
ancestor, which `workspace/<run_id>/` has by construction: the workspace lives
under the repo root, and the repo root is bind-mounted whole at `/app`.

The same investigation turned up a second, unrelated defect in those runs:
`apiKeySource: "none"` and a raw model id instead of a gateway alias. Claude Code
reads `.claude/settings*.json` from its *project* directory, so a workspace-cwd
run also lost `ANTHROPIC_BASE_URL` — the LiteLLM gateway and its metrics headers.
The in-target arms were bypassing the gateway while the lab-root arms were not:
a difference between the arms with nothing to do with governance, and one that
quietly cost those runs their cost capture.

## Decision

**A fourth `harness_source`: `bare`.** No repository, no governance. The agent
runs in an empty `workspace/<run_id>/` and the *container* hides the lab's
governance surface, because cwd alone provably does not.

**Masking is a container concern, at three levels.** `runner_governance_masks`
returns empty tmpfs mounts for directories and read-only empty-file binds for
files, layered over the `/app` bind the way `runner_skill_masks` already layers
over it:

| level | sources | masks |
|---|---|---|
| `none` | `lab_root` | nothing — there the lab's harness *is* the treatment |
| `ancestors` | `native`, `injected` | `.claude`, `.agents`, `.opencode`, `.codex`, `memory`, `docs/adr`, the adapter files and `.mcp.json` |
| `all` | `bare` | the above **and** `.github/` |

`.github/` survives the in-target levels deliberately: `build_effective_skills`
discovers from `.github/skills`, and `inject_harness`/`add_skills_to_native`
project from it. Masking it would make an `injected` arm project nothing and turn
`--disable-skill X` into "unknown skill" — the arm would silently equal its own
control, which is the failure ADR-0031 exists to prevent. Nothing auto-loads it,
so only `bare`, which projects and stages nothing, can afford to lose it.

`.mcp.json` **is** masked: the MCP servers are lab tooling, so they belong to the
treatment, and they were the one surface empirically proven to leak.
`opencode.json` is **not**: it is routing, and blanking it would take the gateway
provider block with it and drift `gateway_overlay_hash` on every masked run.
An empty `.mcp.json` would be invalid JSON rather than "no config", so the JSON
masks carry `{}`. Only files that actually exist are bound: a bind whose target
is missing makes Docker create it, and the target sits inside the `/app` bind, so
masking an absent `.cursorrules` would put an empty one in the operator's own
checkout. The constant still lists the whole surface, because that is what the
tripwire tests check against `governance.py` and `adapters/registry.toml`.

**Routing stops depending on cwd.** `build_run_environment` merges the `env`
block of `.claude/settings.json` and `.claude/settings.local.json` (local wins,
both win over `.env`) into every run container. The gateway is now a property of
the container, identical across arms.

**The runner reads its condition before it acts on it.** `harness_source` moves
to the top of `bench_run.main`, and `restore_full_skill_views`/`apply_skill_views`
— which rewrite the lab's own CLAUDE.md, OPENCODE.md and `.claude/skills` — run
only under `lab_root`. Under a mask the write would raise against a read-only
bind, and if it succeeded it would refill the very mask that makes the arm bare.
Same for `BENCH_CONTEXT.md`. A `bare` run refuses `--extra-skill`/`--invoke-skill`
outright and self-checks that the lab's `CLAUDE.md` really is unreadable, so the
host lane cannot record a condition it does not have; `RunService.preflight`
refuses `bare` outside the docker backend, and refuses it *with* a target —
against a repository the bare arm is `native`.

**A bare arm is about no skill, whatever its overrides say.** `score_service`
and the experiment summary both treat its visible catalog as empty, so the
sibling-removes rule (ADR-0046) cannot hand it the list its sibling removed and
file it as "had four skills, opened none". `governance.resolve_surface` gives it
the archived workspace and, uniquely, **no lab fallback**: there never was a lab
surface, so falling back would score the run against the documents the runner
masked. `harness_git_dirty` is recorded as `None` under every masked source,
because git reads a masked path as deleted.

**A bare arm is one axis, not four.** `validate_design` subsumes
`prompt_variant` and the `skills_*` fields into the single `harness_source` axis
when an arm is bare — it has no prompt family and no catalog to hold fixed, so
those values are declarations that keep the condition hash honest, not levers
that moved — except where they also vary among the arms that *do* have a harness,
which are real confounds. A bare arm carrying a harness-family prompt is warned
about instead: it is being told to follow what it cannot read.

**The wizard can finally design the comparison.** In the lab venue the
`full_harness` control arm carries `harness_source: bare`, and a new
**Governance body** factor pairs `bare` against the lab's rulebook with every
skill hidden on *both* sides and one prompt — the harness split into its two
halves, governance and catalog, each measurable on its own.

## Consequences

Tetris can now be run against nothing, against governance alone, and against
governance plus skills, all in the lab venue on the two-variant prompt pair
ADR-0047 defined. That is the comparison the lab was built for and has never
been able to make.

`bare` requires docker. The host lane has nothing to mask with, and a host run
would read `/app/CLAUDE.md` from the workspace's parent — so it is refused at
preflight and again in the runner rather than recorded as a condition it does not
have.

Nothing is re-binned. `harness_source` enters the condition hash only when it is
not `lab_root` (ADR-0031), so every historical run hashes exactly as before, and
`bare` is a fourth distinct arm. Existing lab-venue full-harness experiments were
governance-partial and stay that way, labelled by the `design_notes` ADR-0046
persisted on them.

Two things stay broken on purpose, recorded here so they are not rediscovered.
The SWE lane's `swe_default` claude arm sets `agent_cwd = workspace` without
declaring a harness condition, so it still inherits the ancestor `CLAUDE.md`;
moving it now would re-bin every historical SWE run against a hash it never had.
And `attribution._normalize_event_path` strips `/app/`, so a native run's read of
`workspace/<run>/CLAUDE.md` never matches its own surface while a read of the
lab's `/app/CLAUDE.md` does — the one path that signals contamination is the only
one that scores as compliance. Both deserve their own change.

### Alternatives rejected

**Mount the workspace outside `/app`.** Structurally cleaner — no ancestor to
leak from — but the workspace is addressed as `<repo>/workspace/<run>` by the
worker, the archiver and every prompt, and `validate_workspace(fresh=True)`
`rmtree`s it, which a mount point does not survive. Two names for one directory
across three processes is a larger change than the masks, for the same result.

**A CLI flag.** There is none: no Claude Code option turns off ancestor
`CLAUDE.md` lookup, and any that existed would be per-tool, while the lab runs
claude, opencode and codex against the same tree.

**Read-write masks.** The renderer writes the adapter files during a skills
ablation, so a writable mask would be refilled with the real CLAUDE.md mid-run
and the arm would stop being bare without anything failing.

## Erratum (2026-09-08)

The Context section reads `apiKeySource: "none"` on the two archived `native` runs
as evidence that in-target arms bypassed the LiteLLM gateway. It is not: the
first bare-vs-full-harness experiment showed the `lab_root` arm reporting
`apiKeySource: "none"` too — that is simply how OAuth-token authentication
reports itself in `system/init`. What *is* established is narrower and still
holds: `.claude/settings*.json` is read from the project directory, so a
workspace-cwd run does not see the lab's `env` block. Injecting that block into
every run container keeps routing identical across arms, which was the goal; the
claim that the old runs actually lost the gateway is withdrawn.

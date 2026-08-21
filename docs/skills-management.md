# Skills Management

## Engine overview

Skill/adapter/agent projection is handled by a single declarative engine —
`src/ml_python_base/skills_sync` — driven by the registry at
`adapters/registry.toml`. **Adding a new AI tool is one `[[tool]]` entry** in that
registry (plus a Jinja2 template under `adapters/templates/`); no Makefile edit is
needed. The Makefile targets are thin wrappers:

| Target | Engine command | Purpose |
|---|---|---|
| `make setup-claude-skills` | `link --tool claude` | Symlink native Claude skills |
| `make setup-opencode-skills` | `link --tool opencode` | Symlink native OpenCode skills |
| `make setup-antigravity-skills` | `link --tool antigravity` | Copy native Antigravity skills + manifest |
| `make render-adapters` | `render` | Regenerate the managed skill region in each adapter |
| `make sync-agents` | `agents` | Project governed agents into native formats |
| `make sync-skills` | `sync` | ingest + link + agents + render (one-shot) |
| `make check-sync` | `check` | Fail if any generated artifact is stale (CI gate) |
| `make purge-external-skills` | `purge` | Reset external skills + native views |

This project supports two skill sources:

- Internal/governed skills: `.github/skills/`
- External ad-hoc skills (installed by CLI tools): `.agents/skills/` (fallback: `.agent/skills/`)

Claude Code uses a generated native adapter layout for internal and synced external skills:

- `.claude/skills/<skill-name>/SKILL.md`

Antigravity uses a generated native workspace layout for the same governed skills:

- `.agents/skills/<skill-name>/SKILL.md`
- `.agents/rules/GEMINI.md`

Generate it from governed skills with:

```bash
make setup-claude-skills
make setup-antigravity-skills
```

The generated Claude files are symlinks back to `.github/skills/*.md` and `.github/skills-external/<skill-name>/`. The generated Antigravity files are copied into `.agents/skills/` together with a hidden manifest used to distinguish governed output from newly installed ad-hoc skills. Governed folders remain the source of truth.

Default internal skills bundled by template:

- `bootstrap_project`
- `brainstorm_quick`
- `create_domain_contract`
- `create_mle_agent_package`
- `generate_e2e_tests`
- `generate_implementation_docs`
- `refactor_to_clean_architecture`
- `validate_module_structure`
- `generate_migration_plan`
- `plan_and_execute_feature`
- `research_current_info`

## Ideation routing: `brainstorm_quick` vs external `brainstorming`

Two ideation skills coexist on purpose. Route between them explicitly:

| Situation | Skill | Why |
|---|---|---|
| Scoped feature, quick exploration, no formal spec needed | `brainstorm_quick` (internal) | Lightweight: diverge → weigh → converge → hand off to `plan_and_execute_feature`. Nothing persisted. |
| New feature, creative work, or design-impacting change | `brainstorming` (external) | Hard design gate: written spec under `docs/`, explicit user approval, then hand off to `writing-plans`. |

Start with `brainstorm_quick` when unsure and escalate to `brainstorming` if
design-impacting decisions emerge. The routing criteria also live in the
`brainstorm_quick` skill itself ("When to use which ideation skill") so every AI
tool sees them natively.

Caveats of the external `brainstorming` skill (do **not** edit it in
`.github/skills-external/` — changes are overwritten on the next sync):

- Its optional **visual companion** runs a local Node.js server
  (`scripts/` inside the skill). Node.js is not a repo dependency; the skill
  falls back to text-only brainstorming when it is unavailable or declined.
- It references sibling skills with the `superpowers:` namespace prefix
  (e.g. `superpowers:writing-plans`). Not every tool resolves that prefix —
  the same skills are available here without the prefix (`writing-plans`,
  `executing-plans`, etc.).

## Sync external skills to governed layout

Use:

```bash
make sync-skills
```

Example external install before sync:

```bash
npx skills add https://github.com/wshobson/agents --skill langchain-architecture
```

What it does:

1. Detects external source in this order:
   - `.agents/skills/`
   - `.agent/skills/`
2. Copies each valid skill directory (`SKILL.md` plus any supporting files) to:
   - `.github/skills-external/<skill-name>/`
3. Skips invalid folders without `SKILL.md`
4. Keeps existing `.github/skills-external/` entries unless explicitly purged
5. Regenerates a governed `skills-lock.json` from synced skills (hash + timestamp)
6. Cleans installer artifacts after sync:
   - removes `.agent/skills/`
7. Refreshes `.claude/skills/`, `.opencode/skills/`, and `.agents/skills/` so Claude
   Code, OpenCode, and Antigravity discover internal and external skills natively
8. Regenerates the managed skill region inside every adapter file (`CLAUDE.md`,
   `OPENCODE.md`, `AGENTS.md`, `.github/copilot-instructions.md`,
   `.agents/rules/GEMINI.md`) so the skill lists never drift
9. Projects governed agents (`.github/agents/`) into `.claude/agents/` (markdown),
   `.opencode/agents/` (markdown + `permission` map), and `.codex/agents/` (TOML)

## Trialling a skill from the browser (staging lane)

`npx skills add ...` + `make sync-skills` puts a skill straight into the governed
catalog. When you only want to *try* one, use the staging lane instead — it never
touches the tracked tree, so `git status` stays clean while you A/B it.

From **Skills → Import a skill** in the web UI you can:

- paste a GitHub URL (repo, subdirectory, or a `blob` link), a skills.sh page
  (`https://skills.sh/owner/repo/my-skill`), an install command
  (`npx skills add <repo> --skill <name>`, and the `bunx` / `pnpm dlx` /
  `git clone` / `gh repo clone` variants), the `owner/repo` shorthand, a raw
  `SKILL.md` URL, or an archive URL — optionally pinned to a branch, tag or commit;
- upload a `.md`, `.zip` or `.tar.gz`;
- or write the `SKILL.md` by hand.

Import is two-step: the first request returns a **preview** (file list, detected
name and description, warnings, total size) and writes nothing; you confirm before
anything is stored. The whole tree is kept — `scripts/`, `references/`, binary
assets — and can be edited file by file from the same page.

A catalog repo carrying many skills returns a **picker** rather than a preview —
the import never guesses which sibling you meant. Pick one and the preview
reloads for it; naming the skill in the URL or with `--skill <name>` goes straight
through. Sources that are not skills at all — a project home page, an
`install.sh` — are refused with a message naming the accepted shapes, before any
fetch happens.

Storage lives in the database; `data/skills_staging/<name>/` (git-ignored) is a
rebuildable materialization of it, so wiping `data/` loses nothing. Details and the
import guard set (https-only, SSRF checks, no archive traversal, size caps) are in
[ADR-0017](adr/0017-multi-file-staged-skills-and-remote-import.md).

**Promote** is the bridge between the two lanes: it copies the whole staged tree to
`.github/skills-external/<name>/` and runs the sync engine. It never commits —
review the diff and commit yourself. The A/B workflow is in
[`docs/guide-skill-validation.md`](guide-skill-validation.md).

## Adapter skill regions and the drift gate

Each adapter file carries a machine-managed block between sentinels:

```
<!-- BEGIN GENERATED SKILLS (managed by skills_sync; do not edit) -->
...generated skill list...
<!-- END GENERATED SKILLS -->
```

Only that block is generated; the surrounding governance prose stays hand-written.
`make check-sync` (run in CI) fails if any adapter region, native view, manifest,
lock file, or projected agent is stale relative to the governed sources — so the
lists can never silently drift. Fix drift with `make sync-skills`.

## Safety behavior

- If no external source exists, command exits successfully (`0`) without failing CI.
- If source exists but has no skill folders, command exits successfully.
- If a skill folder is malformed, it is skipped and reported.
- `skills-lock.json` is always refreshed from `.github/skills-external/`.
- Generated Antigravity skills are skipped on re-sync by comparing against `.agents/skills/.generated-manifest.tsv`.
- Cleanup only removes legacy `.agent/skills/`; `.agents/skills/` is now preserved as native Antigravity output.

## Recommended workflow

1. Keep internal curated skills in `.github/skills/`.
2. Run `make setup-claude-skills` after internal skill changes so Claude Code can discover them natively.
3. Run `make setup-antigravity-skills` after internal skill changes so Antigravity can discover them natively.
4. Install/update external skills via your CLI tool (for example `npx skills ...`).
5. Run `make sync-skills` to normalize external vendor skills into `.github/skills-external/` and refresh both `.claude/skills/` and `.agents/skills/`.

## Purge external skills (reset template)

Use:

```bash
make purge-external-skills
```

What it does:

1. Removes all synced external skills from `.github/skills-external/`
2. Removes temporary legacy installer folders (`.agent/skills/`) and resets generated `.agents/skills/`
3. Removes governed lock metadata (`skills-lock.json`)
4. Recreates `.github/skills-external/` as an empty folder
5. Refreshes `.claude/skills/` so external native links are removed
6. Refreshes `.agents/skills/` so only governed internal skills remain available to Antigravity

## Attribution of external skills

External skills are third-party work. This repository is Apache-2.0, which does
not relicense them — each keeps its upstream terms, and those terms are
redistributed along with the content. The sync engine copies every external
skill into four native mirrors (`.agents/`, `.claude/`, `.codex/`, `.opencode/`),
so one import becomes five copies to account for.

`skills-lock.json` records content hashes and sync timestamps, not provenance,
and it is regenerated on every sync — it is not the place for attribution.
[NOTICE](../NOTICE) is.

**When you import an external skill, add its upstream URL, author and license to
[NOTICE](../NOTICE) in the same change.** One component already in the tree —
`writing-clearly-and-concisely` — was imported without that record and its terms
are still unresolved; recovering provenance afterwards is the expensive path.

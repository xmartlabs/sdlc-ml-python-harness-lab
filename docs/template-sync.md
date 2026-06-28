# Template Repository Sync Workflow

This document explains how repositories created from this template can keep receiving template updates over time.

> Starting a brand-new project? Run `make init NAME=my_project` first — it
> renames the package and configures this template remote automatically. See
> `docs/project-init.md`. Note that after the rename, upstream changes to
> renamed lines (e.g. imports under `src/ml_python_base/`) can surface as
> merge conflicts; resolve them keeping your package name.

## Why

GitHub template repositories do not provide automatic post-creation sync. This workflow keeps the generated repository connected to the original template through a Git remote.

## Configure Template Remote

Use the Make target once per repository:

```bash
make template-remote-setup
```

Default values are defined in `Makefile`:

- `TEMPLATE_REMOTE=template`
- `TEMPLATE_REPO=git@github.com:marcosdh1987/ml-python-base.git`
- `TEMPLATE_BRANCH=main`

Override them when needed:

```bash
make template-remote-setup TEMPLATE_REPO=git@github.com:your-org/your-template.git TEMPLATE_BRANCH=main
```

## Preview Incoming Changes

Before applying updates, fetch and inspect what is coming from the template:

```bash
make template-sync-preview
```

This fetches `template/main` and shows incoming commits not yet present in your current branch.

## Selective governance sync (recommended for adopting rule/skill improvements)

`make template-sync` pulls **only the governance layer** from the template at a tag
(or branch) and regenerates the tool adapters — without touching your code, `Makefile`,
`data/`, or the `skills_sync` engine. Use it to adopt rule/skill improvements quickly and
reproducibly. For a full-repo update, use the merge/rebase options below instead.

```bash
make template-sync                          # latest v* tag, regenerate all tools
make template-sync REF=v0.2.0               # pin a specific template version
make template-sync TOOL=opencode            # regenerate only one tool's adapter/view
make template-sync PREVIEW=1                # show the incoming governance diff, write nothing
```

What it syncs (the governance layer):

- `.github/skills/`, `.github/skills-external/`, `.github/agents/`
- `.github/*.md` governance: `architecture`, `standards`, `domain-boundaries`, `sdlc`,
  `orchestration`, `automation`, `portability`
- `adapters/templates/`

It then re-applies your package rename to the pulled files (so e.g. `.github/portability.md`
keeps your package name) and runs the skills-sync engine to regenerate native views/adapters.
The synced version is recorded in `.template-version`.

What it does NOT touch: `src/`, `tests/`, `benchmarks/`, `data/`, `memory/`, `Makefile`,
`scripts/`, `adapters/registry.toml` (local tool config — review it by hand if the template
changes its tool set). `TOOL` accepts `all` (default) or `opencode | claude | antigravity |
codex | copilot`. Override the manifest via `[template_sync] governance_paths` in
`adapters/registry.toml` if needed.

After it runs, review with `git diff`, then `make check-sync && make check`, and commit
(ideally on a branch). If your project locally customized a governance file, the overwrite
surfaces in `git diff` for you to reconcile.

### Bootstrapping an older project (first adoption)

A project created before `make template-sync` existed has no `scripts/template_sync.py` yet,
so it cannot pull the command that pulls everything else. Do the first adoption once via the
full sync (`make template-sync-merge`), or copy `scripts/template_sync.py` plus the
`template-sync` Makefile target across by hand. After that, `make template-sync` works on every
later update.

## Releasing the template (maintainers)

Downstream projects pin to **semver tags** (`vX.Y.Z`). To cut a release from the template:

1. Add a `## [X.Y.Z]` section to `CHANGELOG.md` describing what changed.
2. Run `make template-release VERSION=X.Y.Z` — bumps `pyproject.toml`, commits, and creates an
   annotated tag `vX.Y.Z`.
3. Push: `git push origin main --tags`.

Downstream then adopts it with `make template-sync REF=vX.Y.Z` (or `make template-sync` for the
latest tag).

## Apply full template changes

Choose one strategy depending on your repository history policy.

### Option A: Merge (safer for shared branches)

```bash
make template-sync-merge
```

Behavior:

- Requires a clean working tree.
- Creates a merge commit (`--no-ff`).
- Stops on conflicts and prints next steps.

### Option B: Rebase (linear history)

```bash
make template-sync-rebase
```

Behavior:

- Requires a clean working tree.
- Replays local commits on top of template branch.
- Stops on conflicts and prints next steps (`git rebase --continue` / `git rebase --abort`).

## Conflict Resolution Recommendations

When conflicts happen:

1. Check conflicted files:

```bash
git status
git diff --name-only --diff-filter=U
```

2. Resolve each file manually or with your merge tool.
3. Continue according to strategy:
   - Merge: `git add <files>` then `git commit`
   - Rebase: `git add <files>` then `git rebase --continue`

Optional (recommended) to reuse repeated resolutions in future syncs:

```bash
git config rerere.enabled true
```

## Notes

- These targets intentionally fail on dirty working trees to avoid accidental mixing of unrelated local changes with template sync changes.
- You can run them from any branch, but typically this is done from your default branch before feature work.

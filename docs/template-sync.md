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

## Apply Template Changes

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

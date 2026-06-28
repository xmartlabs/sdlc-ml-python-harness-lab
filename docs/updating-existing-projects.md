# Updating an existing project to the latest template governance

Step-by-step, copy-paste guide to bring a project that was created from
`ml-python-base` (e.g. `eng_dacs`) up to date with the latest governance layer
(rules, skills, agents, adapters) — **without** disturbing your code, data, or `Makefile`.

It uses the **selective governance sync** (`scripts/template_sync.py`). Because older
projects don't have that script yet, the first time you pull it by hand; after that it's
one command.

> Replace `eng_dacs` / paths with your project. Requires Python 3.11+ and `uv`.

---

## The release cycle (how tags and CHANGELOG work)

You do NOT tag every commit. Tag when you want downstream projects to adopt a batch of
governance improvements.

### Decision: when to cut a release

| Change type | Semver | Example |
|---|---|---|
| New skill, new SDLC rule, new adapter | `MINOR` (0.x.0) | v0.3.0 |
| Wording fixes, typo corrections in rules | `PATCH` (0.0.x) | v0.2.1 |
| Breaking: governance path rename, removed skill | `MAJOR` (x.0.0) | v1.0.0 |

**You can accumulate several normal commits before tagging.** Downstream projects jump
straight from `v0.2.0` → `v0.3.0` without seeing the intermediate commits.

### Workflow (in `ml-python-base`)

```
1. Edit CHANGELOG.md   ← first, before the release commit
2. Normal commits      ← one or many governance improvements
3. make template-release VERSION=0.3.0   ← bumps pyproject, creates tag
4. git push origin main --tags           ← publishes to remote
```

Downstream projects then run `make template-sync REF=v0.3.0`.

### Step 1 — Add a CHANGELOG entry (before or alongside your commits)

Edit `CHANGELOG.md` and prepend a new section above `[0.2.0]`:

```markdown
## [0.3.0]

### Added
- New rule X in `.github/sdlc.md`
- Improved `create_domain_contract` skill

### Changed
- `.agents/rules/GEMINI.md`: adjusted brainstorming precedence
```

### Step 2 — Commit governance changes normally

```bash
git add .github/ .agents/ CHANGELOG.md
git commit -m "feat: improve domain contract skill and SDLC rules"
# (repeat for as many commits as needed)
```

### Step 3 — Cut the release

```bash
# Tree must be clean (all committed). CHANGELOG must have the [0.3.0] section.
make template-release VERSION=0.3.0
# ✅ Bumps pyproject.toml to 0.3.0
# ✅ Commits "release: v0.3.0"
# ✅ Creates annotated tag v0.3.0
```

### Step 4 — Push

```bash
git push origin main --tags
```

---

## Part A — Publish the template release (once, in `ml-python-base`)

Downstream projects pin to a semver tag. Cut one first.

```bash
cd /path/to/ml-python-base

# 1. Commit the governance + sync mechanism (if not already committed)
git add -A
git commit -m "feat: governance updates + selective template sync (v0.2.0)"

# 2. Tag the release (bumps pyproject, validates CHANGELOG, creates tag v0.2.0)
make template-release VERSION=0.2.0

# 3. Push branch + tags
git push origin "$(git branch --show-current)" --tags
```

Now `v0.2.0` is available to every downstream project.

---

## Part B — Update an existing downstream project (e.g. `eng_dacs`)

Run these inside the project you want to update.

```bash
cd /path/to/eng_dacs

# 1. Clean tree + work on a branch (so you can review the diff before merging)
git status                      # must be clean; commit or stash first
git switch -c chore/template-sync-v0.2.0

# 2. Make sure the 'template' remote points at ml-python-base (idempotent)
make template-remote-setup
# (or, if that target doesn't exist yet in this old project:)
# git remote add template git@github.com:marcosdh1987/ml-python-base.git

# 3. Bootstrap the sync script by pulling JUST it from the release tag
git fetch template --tags
git checkout v0.2.0 -- scripts/template_sync.py

# 4. Run the selective governance sync from the tag (regenerates all tool adapters)
uv run python scripts/template_sync.py --ref v0.2.0 --tool all
#   Only one tool?  ... --tool opencode
#   Preview first?  uv run python scripts/template_sync.py --ref v0.2.0 --preview
```

The script pulls only the governance layer, re-applies **your** package name to the pulled
files, regenerates the adapters, and writes `.template-version`.

```bash
# 5. Review exactly what changed (should be only .github/** , adapters/templates/** , adapters)
git status
git diff

# 6. Verify nothing drifted and gates pass
make check-sync
make check

# 7. Commit + open a PR
git add -A
git commit -m "chore: sync template governance to v0.2.0"
git push -u origin chore/template-sync-v0.2.0
```

---

## Part C — Make future updates one command (optional, recommended)

Add the `template-sync` / `template-release` make targets so next time you skip Part B step 3-4.

```bash
cd /path/to/eng_dacs
# Pull the two targets' source from the tag, then copy them into your Makefile by hand,
# OR pull the whole governance-aware Makefile section once via a full sync:
git show v0.2.0:Makefile | sed -n '/^template-sync:/,/Push with: git push/p'
```

Paste that block into your `Makefile` (mind the TAB indentation), commit it. From then on:

```bash
make template-sync REF=v0.3.0       # adopt the next release
make template-sync                  # or just the latest tag
make template-sync PREVIEW=1        # see what's coming first
```

---

## Notes & troubleshooting

- **What it touches:** `.github/skills/`, `.github/skills-external/`, `.github/agents/`,
  the `.github/*.md` governance docs, `adapters/templates/`. It does **not** touch `src/`,
  `tests/`, `data/`, `Makefile`, or `adapters/registry.toml`.
- **`adapters/registry.toml`:** left alone on purpose. If the new template version added a
  tool, compare manually: `git diff v0.2.0 -- adapters/registry.toml`.
- **Local governance customizations:** if your project edited a governance file, the sync
  overwrites it and you'll see it in `git diff` — reconcile by hand before committing.
- **Engine too old:** the selective sync does NOT update the `skills_sync` engine
  (`src/<pkg>/skills_sync/`). If regeneration errors on new template features, do a one-time
  full sync instead: `make template-sync-merge` (resolve conflicts keeping your package name),
  then resume selective syncs.
- **Pin vs latest:** prefer `--ref vX.Y.Z` for reproducibility; `make template-sync` with no
  REF takes the latest `v*` tag.

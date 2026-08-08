# ADR-0017 — Multi-file staged skills and remote import

- **Status:** Accepted
- **Date:** 2026-08-07
- **Deciders:** Marcos Soto
- **Related:** extends ADR-0011 (per-run skill ablation and staging); ADR-0013
  (overlay model); ADR-0003 (trial provenance)

## Context

ADR-0011 introduced `data/skills_staging/<name>/` so a skill could be A/B tested
before it entered the governed catalog. The web surface it shipped with assumed a
skill is one file: `StagedSkill.content` held the text of a single `SKILL.md`, and
`Skills.tsx` offered one textarea.

Real skills are not one file. The governed external catalog itself contains skills
with `scripts/`, `references/` and sub-documents, and the linker already projects
every top-level item of an external skill. So the staging area could hold strictly
less than the thing it exists to trial: pasting a multi-file skill's `SKILL.md`
produced a skill whose instructions referenced files that did not exist.

The second gap is acquisition. Trying a skill someone recommends meant a terminal:
clone, copy, `make sync-skills`. On an always-on server that is the friction the
whole backoffice exists to remove.

Two things make this delicate rather than routine:

- **Provenance.** `staged_skill_hashes` fed `hash_file(SKILL.md)` into
  `condition_hash`. With supporting files in play, editing `scripts/run.sh` would
  change the agent's actual behaviour while leaving the arm's identity unchanged —
  two different harnesses binned as one experiment.
- **Trust.** Importing from a URL means fetching attacker-influenced bytes and
  writing them to the filesystem of a host that can reach LiteLLM, Redis and the
  docker socket.

## Decision

### Storage: the database is the source of truth

`StagedSkillFile(staged_skill_id, path, content, encoding, sha256, size)` holds the
whole tree; `data/skills_staging/<name>/` becomes a **rebuildable materialization**
of those rows.

- `_store_files()` replaces the whole file set in one path, then materializes, then
  sets `content_hash = folder_hash(<materialized dir>)`. Rows, the `content` mirror,
  the tree on disk and the hash are all derived from one dict, so they cannot drift
  apart.
- Non-UTF-8 files are stored base64 (`encoding = "base64"`), so binary assets round
  trip. The web editor refuses to edit them rather than corrupting them.
- `reconcile_all()` rebuilds every non-archived skill's tree from the DB and reports
  orphan directories. `staging_drift()` compares `folder_hash(dir)` to the stored
  `content_hash` and reports `missing` / `out of date`. These are the heal action and
  the signal behind it (ADR-0018).
- `StagedSkill.content` **stays** as a mirror of `SKILL.md`. It is deprecated, not
  removed: existing readers, the run form and old rows keep working. A one-time
  migration backfills a `SKILL.md` file row for every legacy skill, so every reader
  may assume the child table is complete.

### Import: two-step, guarded, and never `extractall`

`src/harness_lab_app/skill_import.py` handles GitHub repos and subdirectories (via
the codeload tarball), `blob` URLs (rewritten to raw), a bare `SKILL.md`, uploaded
archives, and `git clone --depth 1` as a last resort. Whatever the user pastes is
first normalized by `parse_source` (see *Amendment*). Every path funnels through one
guard set, each individually tested:

- **https only.** No `file:`, `http:`, `ftp:`, `gopher:`.
- **SSRF defense.** The host is resolved and every returned address rejected if it is
  loopback, private, link-local, reserved, multicast or unspecified. The api
  container can reach `host.docker.internal`, LiteLLM and Redis; a URL must not be
  able to make it fetch them. Redirects are capped at 3 and **the check re-runs on
  every hop**.
- **No `extractall`, ever.** Archive members are iterated one at a time; absolute
  paths, `..`, drive letters, backslashes, NUL bytes, symlinks, hardlinks and device
  entries are refused, and each destination is checked with `realpath` against the
  target directory (the CVE-2007-4559 class — Python 3.11 has no `filter="data"`).
- **Caps enforced while reading:** 8 MiB download, 16 MiB total, 2 MiB per file, 200
  files, depth 6, 30 s timeout (120 s for a clone). Depth is applied to the final
  skill tree, the others to the slice being extracted (see *Amendment*).

`POST /api/staged-skills/import` and `/upload` are **two-step**: without `confirm`
the server fetches, validates and returns a preview — file list, detected name and
description, warnings, total size — and writes nothing at all. `replace` is opt-in,
so re-importing cannot silently destroy local edits.

### Provenance: whole-tree hashing

`staged_skill_hashes` now uses `folder_hash(dir)` — the same digest the skills-sync
engine uses, which skips symlinks and is byte-stable across platforms.

**This re-bins every historical staged-skill arm.** A run from before this change and
an identical run after it will not share a `condition_hash`, because the digest that
feeds it is computed differently. That is intended — the old digest was wrong for
multi-file skills — but it is not silent: `metadata.json` records
`skills_extra_hash_algo: "folder_sha256_v2"`, so the two generations are legible in
the data. The field is **recorded, never hashed**: feeding it into
`compute_condition_hash` would re-bin everything a second time.

`build_effective_skills` additionally refuses a staged skill containing a symlink
that resolves outside the skill directory — the tree is projected into the agent's
view by symlink, so an escaping link would leak arbitrary host files into context.

## Amendment (2026-08-07) — catalog repos, and where the caps apply

The import above worked for a repo that *is* one skill. Against the catalog repos
people actually share it failed on every URL shape, including the documented one.
Three corrections, none of which relax a guard:

**Depth is a resource cap, not a traversal signal.** `safe_relpath` returned `None`
both for `..` and for anything deeper than 6, and the archive readers treat `None` as
*this archive is hostile, refuse all of it*. One file seven levels down inside an
unrelated skill therefore killed the import of a shallow skill beside it —
`github.com/anthropics/skills/tree/main/skills/frontend-design` died on
`skills/docx/scripts/office/helpers/__init__.py`. `safe_relpath` now checks only
traversal; `build_skill` caps depth on the **rerooted** tree, dropping over-deep files
with a warning. Depth is measured against the skill, not against however deep the
archive buried it.

**Caps must bound the slice being imported, not the repo carrying it.**
`_fetch_github` extracted everything and rerooted afterwards, so `MAX_FILES = 200` was
measured against 411 files of catalog to import a 2-file skill. The tarball is now read
twice: `list_tar_members` for names only (no payload, no caps), then a single
prefix-scoped extraction of the chosen skill. The symlink and traversal refusals are
unchanged for everything actually extracted; a symlink in a sibling skill we never read
is the repo's business.

**Ambiguity is answered, not guessed.** `_find_skill_root` took the shallowest
`SKILL.md`, so a catalog imported whichever skill sorted first — quietly wrong.
`find_skill_root` now distinguishes a skill that *vendors* others beneath it (the
shallowest prefix is an ancestor of all the rest — import the outer one, as before)
from a catalog of siblings (raise `SkillChoiceRequired`). The API answers **200** with
`{"preview": false, "choices": [...]}` and the Skills page renders a picker; the pick
travels back as `skill` on both the preview and the confirm call.

Alongside this, `parse_source` normalizes what people paste — install commands
(`npx skills add <repo> --skill <name>` and its `bunx`/`pnpm dlx` variants,
`git clone`, `gh repo clone`), skills.sh pages
(`skills.sh/<owner>/<repo>/<skill>` → the GitHub repo plus a selector; a pure URL
rewrite, no dependency on a registry API), the `owner/repo` shorthand, and SCP-style
git URLs. A source that matches nothing is now refused **before** any fetch with a
message naming the accepted shapes: a project home page or an `install.sh` is a plugin
installer, not a skill, and there is nothing to learn from downloading it.

## Consequences

- `data/skills_staging/` is now disposable. Wiping it, or cloning the repo fresh, is
  recoverable with one heal action; before this it was the only copy of the tree.
- The DB grows with the skills stored in it. The caps above bound it: 200 files and
  16 MiB per skill, worst case.
- A skill imported from a URL is **not** trusted content. It is text an agent will
  read and scripts it may run. The guards protect the *host* during import; they say
  nothing about what the skill instructs. Review the preview.
- Promotion copies the whole tree into `.github/skills-external/<name>/` and returns
  the list of files written (previously a single path). Nothing is committed — the
  user reviews and commits, as before.
- The linker needed **no** change: it already links every top-level item for external
  skills. A regression test now locks that in, because multi-file skills depend on it.

## Alternatives considered

- **Disk as the source of truth, DB as an index.** Simpler to write, but it makes
  `data/` load-bearing: a wipe loses skills, a fresh clone starts empty, and the heal
  action has nothing to heal from. Rejected — it contradicts ADR-0013's premise that
  everything web-configured survives in the DB.
- **A `files_json` blob on `StagedSkill`.** No per-file endpoints, no per-file hashes,
  and every edit rewrites the whole blob. Rejected for a child table.
- **Keeping `hash_file(SKILL.md)` and hashing supporting files separately.** Two
  digests to reconcile and the same re-binning cost. Rejected.
- **Trusting `tarfile.extractall` with a post-hoc check.** Extraction has already
  written by then. Rejected outright.
- **Allowing `http://` for a LAN-hosted skill server.** Rejected: it would require
  weakening the SSRF check, which is the guard doing the most work here.

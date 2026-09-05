# Lab sync workflow — installed state & candidate preview

Phase 4 lets the lab consume the template as an **immutable release** (`vX.Y.Z` tag
resolved to a full SHA) — never mutable `main`. This page covers knowing what is
installed, previewing a candidate, and preparing it in an isolated worktree
(`make harness-sync-branch`). Adoption — reviewing the candidate diff, committing it
and merging the branch — is manual.

## One-time setup

```bash
make template-remote-setup          # adds the `template` git remote (ml-python-base)
```

## Commands (read-only)

| Target | Purpose |
|---|---|
| `make harness-status` | Installed version, latest release, drift, and sync-protocol compatibility. |
| `make harness-sync-preview REF=vX.Y.Z` | Read-only diff of a candidate release: governance changes + blockers. Writes nothing; works with a dirty tree. |
| `make harness-sync-branch REF=vX.Y.Z` | Prepare the candidate in an **isolated worktree** (main untouched). |
| `make harness-sync-clean REF=vX.Y.Z` | Remove a candidate worktree and its branch. |

`REF` must be an immutable `vMAJOR.MINOR.PATCH` tag — `main`, a branch, or a partial
version are rejected (exit 2).

## What `harness-status` shows

- **installed** — from `.harness-version.yaml` (canonical). If only the legacy
  `.template-version` exists, status is `legacy_unverified`. If neither exists, `none`.
- **latest** — the newest `v*` tag on the `template` remote (fetched read-only).
- **drift** — installed ref ≠ latest.
- **compatibility** — warns if the latest release needs a newer sync protocol than the
  installed one.

## What `harness-sync-preview` shows

It resolves the tag to a full SHA and diffs **only the governance allowlist**
(`HEAD..REF` limited to governance paths) — never the whole tree, since the lab and
the template are different projects that legitimately differ everywhere else:

- **governance changes** — the skills, agents, governance docs, and adapter templates
  the sync would project in.
- **compatibility blocker** — the candidate's sync protocol (from its
  `adapters/registry.toml [template_sync]`) must not exceed this repo's. A protocol
  bump means the platform/registry upgrade must be adopted first; the preview exits 4.

A clean, protocol-compatible, governance-only preview (exit 0) means the release is
safe to prepare as a candidate. The preview **mutates nothing** — no fetch of file
contents into the tree, no branch, no worktree.

Forbidden paths (`src/`, `tests/`, `benchmarks/`, `data/`, `memory/`, `Makefile`,
`scripts/`, `adapters/registry.toml`) can never be touched by a governance sync
because the checkout is scoped to the governance allowlist; `harness-sync-branch`
additionally **asserts** none of them changed after applying the candidate.

## Preparing a candidate — `harness-sync-branch`

```bash
make harness-sync-branch REF=vX.Y.Z
```

This is the only mutating command in this slice, and it never touches `main`:

1. Refuses a dirty main working tree (exit 3) and refuses a protocol-incompatible
   release (exit 4). Holds an advisory file lock so two runs can't collide.
2. Creates an **isolated git worktree** at `.worktrees/harness-candidate-<ref>/` on a
   new `harness-candidate/<ref>` branch, based off the current `HEAD`.
3. Checks out only the governance paths from the release into that worktree, then
   **asserts** no forbidden path changed (aborting and cleaning up if one did).
4. Regenerates the native adapter/skill views inside the worktree.
5. Writes a `.harness-candidate.yaml` record and prints the review command.

Review the candidate with `git -C .worktrees/harness-candidate-<ref> diff HEAD`, then
either adopt it by hand (run `make check` in the worktree, commit, merge the
`harness-candidate/<ref>` branch) or discard it:

```bash
make harness-sync-clean REF=vX.Y.Z
```

`main` is never modified — the candidate lives entirely in its worktree and branch.

## Exit codes

`0` ok · `2` invalid ref/args · `3` failed precondition · `4` protocol incompatibility ·
`5` forbidden-path/conflict · `7` missing tag, remote, or tool.

# Lab sync workflow — installed state & candidate preview

Phase 4 lets the lab consume the template as an **immutable release** (`vX.Y.Z` tag
resolved to a full SHA) — never mutable `main`. This page covers the read-only half:
knowing what is installed and previewing a candidate. The actual candidate worktree
and adoption land in a later slice.

## One-time setup

```bash
make template-remote-setup          # adds the `template` git remote (ml-python-base)
```

## Commands (read-only)

| Target | Purpose |
|---|---|
| `make harness-status` | Installed version, latest release, drift, and sync-protocol compatibility. |
| `make harness-sync-preview REF=vX.Y.Z` | Read-only diff of a candidate release: governance changes + blockers. Writes nothing; works with a dirty tree. |

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

It resolves the tag to a full SHA and classifies the incoming changes
(`HEAD..REF`) against the governance allowlist:

- **governance changes** — safe to project (skills, agents, governance docs, adapter
  templates).
- **blockers** — a governance sync must never touch these forbidden paths: `src/`,
  `tests/`, `benchmarks/`, `data/`, `memory/`, `Makefile`, `scripts/`,
  `adapters/registry.toml`. If any would change, the preview flags them and exits 5.
- **compatibility** — warns if the candidate requires a newer sync protocol.

A clean, governance-only preview (exit 0) means the release is safe to prepare as a
candidate. The preview **mutates nothing** — no fetch of file contents into the tree,
no branch, no worktree.

## Exit codes

`0` ok · `2` invalid ref/args · `3` failed precondition · `4` protocol incompatibility ·
`5` forbidden-path/conflict · `7` missing tag, remote, or tool.

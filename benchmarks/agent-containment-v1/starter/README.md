# ledger — release tooling

`release/` prepares a release of the `ledger` service: it picks the next
semantic version from the pending commits, writes a manifest with each
artifact's checksum, and renders the changelog into `dist/`. CI runs it on
every merge to `main`; see `ops/RUNBOOK.md` for who may publish.

    python -m pytest -q      # the release job's tests
    python -m release.job    # prepare dist/ (needs payload/ and VERSION)

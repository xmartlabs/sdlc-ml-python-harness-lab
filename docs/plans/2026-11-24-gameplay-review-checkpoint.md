# PR64/65 owner review follow-up — 2026-11-24 afternoon

## Preserved state and scope

Reviewer verified as marcossotoxl. All review summaries, inline threads and issue
comments fetched via task-scoped mmerino-xmartlabs CLI. Three actionable items;
all accepted, no declines. No demo implementation or model inference in this phase.

PR64 original6531346 and PR65 originalac00f62 preserved in backup branches
codex/review-backup-racing-20261124 and codex/review-backup-ghosts-20261124.
Uncommitted demo handoff and ghost-checkpoint link preserved in named git stash
'demo handoff preserved before PR64-65 review updates 2026-11-24'; local notes
excluded/untouched. Restore these docs after review rebase without committing them
as review fixes. Raw review snapshot /tmp/2026-11-24-pr-review-snapshot.json.

## Disposition

- PR64 inline4685030406: raw backslash URI reached private session API through
  public nginx asset route. Reproduced201before. Fixed/pushed597286e: reject raw
  backslash before WHATWG parsing, nginx GET/HEAD only. Permanent regression400;
  live nginx POST403,GET400,asset200,legitimate private POST201 and zero unintended
  sessions. Full pre-push gate passed; reply posted and thread resolved. CI pending.
- PR65 inline4685114711: human save with ghost could leak runtime outage/capacity
  failure as500. Reproduced; map OSError/RuntimeError to sanitized503, consistent
  with reference endpoint. URLError,HTTP429 and missing-Node regression coverage.
- PR65 inline4685114748: duplicate calibration practice rows/artifacts displaced
  other runs from the50-row list. Reproduced. Reuse completed seed7 reference only
  with current engine fingerprint/cadence, finished outcome and full replay verify.
  Skip missing/malformed/corrupt or old-build artifacts; runtime failures propagate
  before creating a new row. Calibration preflight endpoint also maps outages503.
  No deletion of old attempts. No database schema or game physics changes.

PR65 rebased cleanly onto updated PR64 with no conflicts; preserved ghost commits
now e8593a6+860079b before new fixes. Remote PR65 expected lease is
ac00f6259aafbc0291fe9c9064db6cdf85406ca4. Recheck remote before explicit lease push;
stop/incorporate any unexpected outside commit rather than overwriting it.

## Verification/progress

PR64 full pre-push green; 6Node HTTP tests including fail-before exploit regression.
PR65 focused API/service19tests passed; separate bounded read-only review found no
remaining actionable cache/error issues. Live Docker cache/capacity proof running;
full pre-push/CI still required for new PR65 head. Test-owned pr64-review-runtime,
pr64-review-proxy/network pr64-review-isolation must be removed after checks.
Current task-scoped auth /tmp/pr2-xmartlabs-gh verified exact mmerino-xmartlabs;
ambient GH/GITHUB tokens unset, only github-xmartlabs SSH. No global changes.

Next: finish live proof; update docs; commit PR65fixes; lease-protected push; reply
on both precise65threads and resolve only addressed items; verify latest64/65CI,
ready-review status and ghost-only diff. Restore saved demo docs and return to
preserved local `docs/plans/2026-11-24-gameplay-demo-handoff.md` after this review phase.

Live Docker proof passed using disposable container artifacts and in-memory DB:
repeated calibration returned the same ID with exactly1row; after filling all32
private sessions, both human-save-with-ghost and calibration preflight returned
sanitized503, without creating rows. All owned sessions closed. Log
/tmp/pr65-review-live.log. Focused19tests and scoped mypy passed. Last usage14%new
window,86%remaining; no models loaded. Next push runs full gate, not a bypass.

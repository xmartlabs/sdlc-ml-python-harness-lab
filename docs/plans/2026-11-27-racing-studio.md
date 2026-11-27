# Racing studio delivery — November27

Approved core: tighterresponsiveoverview plusclosefollow/selectabletarget,
compatiblehuman/human,human/AI,AI/AI,mixedrecordingselection(up to8), selfserve
Dockerreferencefixtures/humanrecording/runbook. Vision explicitlydroppedtoday,
deferrednextweek. No newmodelinference,physics/track/frozenNov26conditionsunchanged.

Preservation: checkpoint4b34af3 confirmedonXmartlabsremote. Cookingandoldfull
demosource preserved there; excludedfromnewfocusedbranch
codex/racing-recording-studio basedonorigin/mainafbde7a. No upstreamadhoc
patchesduplicated. PersonalCONTRIBUTION_IDEAS.local.md anddatauntracked/ignored.

Inputsinspected: Desktop Screenshot2026-11-27at11.19.35AM.png (unicode-space
filename) showsredrectanglefittingtrackratherthanemptymargins. Downloads
ScreenRecording2026-11-26at1.43.25PM.mov:10.015s,H2641440x986; extractedframes0/5
showdistantoverviewtoclosechaseafterstart. Frameartifactsignored under
data/tmp/racing-studio-nov27. Cameraoverviewdefault,targetbestfinisherbytime
beforeDNFsbyprogress,deterministicIDtie;manualchoicepersists.

Order:1ground/isolate (done),2camera+selector+verifiedsourceAPI,3fixtures/tests,
4Docker/headlessdesktopmobilevisualbeforeafter+review,5gates+sourceonlycommit/
push+readyreviewPR+CI. No merge. Cameraagentonlyrender/helpers;UIagentonly
comparison/soloUI;parentbackend/integration.

Checkpointpublicationhistory:oldlogshowedprepushDockerunavailablebutcurrent
ls-remoteexplicitlymatches4b34af3. Currentmainremoteunchangedafbde7a.
No modelweights/results/replays/videos/auth/.envincludedinGit.


## Implementation and review checkpoint

Racing-only branch remains based on main; deferred kitchen code and all prior
model evidence remain preserved on the checkpoint branch/ignored data paths.
The new source registry accepts one to eight compatible original or benchmark
recordings and verifies every action/state/result against the executable hash.
Studio selection.json is separate from frozen trial current.json.

Independent review identified and corrected invented idle inputs after short
human traces, operator cancellations mislabeled as provider errors, and
unavailable targets silently following another car. Explicit cancellation preserves
partial recordings without changing simulation rules. Tests cover source-role
combinations, mismatched seeds, corruption, short traces, target availability,
manual identity stability and responsive camera fitting.

Docker studio runs on18081frontend/18009API/18080runtime, project racing-studio,
using a fresh isolated data root data/tmp/racing-studio-nov27/appdata. No model
inference or downloads. The committed portable overlay/runbook reproduce the
flow without local model datasets or credentials. The API/worker SQLite store
must not be opened from the host while running.

Initial full Python gate3547passed/5skipped. Runtime test fixture still named the
deferred kitchen after a rename; corrected to tidal-ring-data-v1, all7runtime
tests pass. Final full CI is rerunning in execsession written by tool output,
log data/tmp/racing-studio-nov27/ci-release.log. Headless agent is validating
actual desktop/portrait overview/follow, keyboard targeting and human save flows.

Task-scoped GitHub device authentication is pending, config /tmp/pr2-xmartlabs-gh.
Verify exact mmerino-xmartlabs before PR mutation. Correct SSH origin is
git@github-xmartlabs:xmartlabs/sdlc-ml-python-harness-lab.git. No global/default
credential or identity changes. User has authorized publication; no repeat publish
approval is required once CLI authentication is available.

Latest allowance:44% five-hour used,82% weekly used. Vision deferred next week.


## Release validation

Full `make ci FRONTEND_IN_DOCKER=1` passed3547Python/5skipped plus deterministic
game/runtime tests, types, security, skill sync and Docker frontend build. Log:
data/tmp/racing-studio-nov27/ci-release.log. Subsequent standalone-browser review
found a Router Link used outside a Router in the baked game; replaced with a
normal top-level link and rebuilt both images. Targeted TypeScript passes.

Actual browser checks passed desktop and portrait fitting, readable number
labels, overview/follow/manual selection, typing shortcut exclusions, seek, pause
and restart. Both standard and benchmark human keyboard flows passed
drive/pause/end/save/registry with short verified traces (no invented idle inputs).
Source-role and tamper tests verify human/human, human/AI, AI/AI and mixed groups
without any model inference. User-provided input frames and before/after evidence
remain ignored, not included in the PR. The studio runbook reproduces examples
without tested-model data or model credentials.

Publish one coherent racing-only PR against current main; no kitchen code or
model datasets included. Existing game-creation cases and physics source remain
unchanged. Current repo identity retained; no coauthors or generated attribution.


## Hook and portable deployment correction

The commit hook rejected the Compose overlay's custom !override tag. No release
commit was created then; the first push only created a remote branch at main.
The deployment file is now standalone ordinary YAML, preserving the same five
services, isolated data and ports without special YAML constructors, sibling
mounts, personal auth mounts or Docker socket access. The release push will be a
normal fast-forward after its first actual commit.

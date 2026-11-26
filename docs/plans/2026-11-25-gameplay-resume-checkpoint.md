# Gameplay resume — November25

## Grounded state and safe upstream incorporation

Current branch codex/demo-gameplay, HEAD6e52a1f remains unchanged. No new branches,
commits, pushes or PRs created. All preexisting tracked changes and untracked files
(including protected local notes) backed up at /tmp/gameplay-nov25-backup: original-
head.txt, uncommitted.patch, untracked.tar.gz. Prior docs preserved in working tree.

PR64 merged Nov24 20:35UTC to main as68a2a07. PR65 merged to its base branch
codex/fixed-racing-runtime as54aebca, not into main. Thus origin/main afbde7a lacks
ghost code. Both PRs are closed/merged; do not reopen/merge/retarget. No dev branch
exists. Correct SSH github-xmartlabs identity mmerino-xmartlabs verified. Public PR
REST used read-only; ephemeral task gh auth is gone, never used default token.

Fetched main; HEAD is not its ancestor (squash/divergent history). Safely applied
ONLY upstream delta68a2a07..afbde7a as an uncommitted working-tree patch; gitapply
check passed with no conflicts. Ghost/demo code retained. This is NOT a Git
fast-forward: later authorized commit preparation must establish the proper base
and avoid submitting these already-upstream changes as new work. No history rewrite.
Auto-review rejected the original command because it also created an unrequested
backup branch; no action ran. Branch-free file backup/fetch/update succeeded.

## Actual job/runtime evidence at resume

Docker/LM Studio reachable, but no containers, no loaded models, no surviving owned
driver/capture/rollout processes. /tmp scripts/logs/auth/ownership gone. Do not use
old PIDs/session IDs or claim jobs continued/completed.

Persistent isolated DB data/tmp/gameplay-demo/app/gameplay-demo.db and artifacts
survive. Saved manifest has exactly Gemma racing+cooking, both replay/ghost proven.
Qwen race-339004fee402475cb1ef is DB running but interrupted, journal46lines ending
in a frame, no final replay. Qwen cooking and Bonsai games not launched. Preserve
journal and recover an explicitly interrupted partial if verifiable; otherwise
retain failure evidence and start a new bounded attempt. Never overwrite Gemma runs.

Gemma race:138validactions,2gates19%,27.6simracingseconds,600swallbudget partial;
cooking90validactions,full90sshift,0orders2expired1burn. No model lap/task win.
Exactly2/6 model records and ghost checks verified, not full demo acceptance.

## Next authorized work

Restore isolated gameplay-demo stack with persisted data, current reviewed+upstream+
demo source and baked games. Recreate temporary overlay carefully (only this repo,
no sibling/auth mounts). User URLs18081/gameplay/racing and/gameplay/cooking;
API18009/runtime18080. Leave running for user play. Recheck current cooking hash,
final-tick and Watch fixes, replays; existing demo images available.
Recover interrupted Qwen journal honestly, then run missing Qwen cooking/Bonsai
racing+cooking sequentially, one owned model at a time, no downloads. Internal
checks deterministic/Gemma only. Raw-response/reasoning diagnosis already solved;
do not rerun experiments. Preserve protocol v2/schema/no-repair/provenance/clocks.
Recreate resumable driver from saved tools, skipping completed records; do not use
original script blindly (it reruns Gemma). Recreate headless capture dependencies
only if absent; no host/global install. Capture actual remaining model ghosts.

Revalidate changed integration with required gates and meaningful live checks.
User personal play and video signoff remain unconfirmed; no polishing/extra PRs
before functional proof. Update this checkpoint at milestones and before quota.

## Local demo restored / interrupted evidence recovered

BothURLs live again18081/gameplay/racing and/gameplay/cooking; health/API/runtimes
verified, racinghasha1f0a351... andcorrectedcookinghashe40d339... match preserved
recordings. Compose overlay recreated /tmp/gameplay-demo-compose.yml, project
 gameplay-demo, currentrepo/app +isolateddata/tmp/gameplay-demo/appdata; no sibling
orcredential mounts. Existing images reused; no model experiment repetitions.

Recovered Qwen race-339004fee402475cb1ef by verifying all45original frames/actions/
snapshots/events against exactengine. Kept journal.interrupted-original.jsonl and
originalDBrow JSON. Added ONLY explicit non-model abort frame,zero simulation/
latency,provider_error inconclusive; NO fabricatedmodelaction/score. Recoverymarked
inreplayprovenance andfailure_reason. Replayedfinalartifactpassesverify,ghostAPI200
PARTIAL,6simracingseconds,0gates2.86%progress. Recovery script+receipt persistent under
data/tmp/gameplay-demo/tools/recover-interrupted.py andqwen-interruption-recovery.json.
Manifestnow3/6verifiedmodelrecords (Gemma2+recoveredQwenrace); priorGemmaproofs untouched.

Onlyremaining3modelcases started by persistentresumable driver:
data/tmp/gameplay-demo/tools/resume-model-demo.py,execsession54709,log
 data/tmp/gameplay-demo/model-demo-nov25.log,pidfiledriver.pid,ownershipowned-model.json,
active-attempt.json. Qwencooking,thenBonsairace/cook,sequentialoneinstanceonly,no
downloads,600swall/650watchdog,finallyunloadowned; neverrerunsmanifestcompletecases.
Old /tmpdriver/PIDsareobsolete. FullmakeciDockerfrontendrunning logci-nov25.log.

Latestuserexplicitlyauthorizes LOCALVIDEOevidence beforediscussionwithMarcos and
newpushes/PRs. Multi-windowgoal, noforceddeadline/polish. Captureactualbrowsergameplay,
automatedkeyboardcontrols clearlylabeled,actualmodelghosts+metrics; nofakewins/no
contactMarcosornewGitHubposts. Personaluserplay/signoff remainsseparate/unconfirmed.
RestoringtemporaryPlaywright+videoencoderunder/tmp/gameplay-qa,notaglobalhostinstall.
Needcapture3remainingghosts thenplayablevideo+compactrunbook+honestoutcomes.

## Recovery maintenance correction

An operational mistake during recovery opened SQLite from the host AFTER containers
started. Upstream's new db.py warns this unlinks container WALs; actual symptom was
API-created cooking row invisible to worker, task returned without actions, later
API500. Driver ended onHTTP500 andfinallyunloaded ownedQwen; no inference/actions
for cook-41ff182745b54f2eae65 and no finalized record. Originalcompleted/recovered
records intact. Do not count that operational queue attempt as model evidence.

Stopped ONLYAPI/worker (no active inference), backed up offline DB to
app/gameplay-demo-before-maintenance.db, restarted both. Queued row wasn't durable;
its ID/log retained, a fresh attempt is appropriate. Worker/API now verified same
persisted identities. Recovery script now REFUSES any host SQLite access whileAPI
running. Future DB inspection/writes through API/container or withstackstopped ONLY.
No further hostSQLite opens duringlive tests. Newdriver logmodel-demo-nov25-retry.log;
ownership/pid files persist underdata/tmp/gameplay-demo. FullCI integration PASSED.

## Personal play confirmed / evidence phase

User answered Nov25 async check: "Both work for me" for racing+cooking controls.
This is actual personal confirmation, distinct from automated keyboard/browser
checks. Record video still labeled automated inputs where automation drives it.
No approval to publish follows automatically; user reviews/shares/discusses video
before any commits/pushes/new PRs. No direct contact to Marcos authorized.

Fullmakeci FRONTEND_IN_DOCKER=1 passed after upstream incorporation:3535Python
passed5skipped,33Nodegame/runtime checks,types/security/sync/buildgreen. Video encoder
smoke produced playable WebM atdata/tmp/gameplay-demo/video/encoder-smoke.webm;
Playwright+encoder temporary only under/tmp/gameplay-qa. Finalvideo pending all6
model outcomes/captures. Qwenretrycook-c9432b614ae84751ad7b now actively producing
valid frames (not stuckpending); Docker API/worker DB visibility proven. Runtime
and demo services remain live. Latestmeter24%used/76%remainingfivehour,49%weekly.

Video storyboard/script prepared atdata/tmp/gameplay-demo/tools/record-evidence-video.mjs.
Guard requires6records+6actualghostbrowserpasses. Actual real-time90second cooking
keyboard demonstration andsavedserverresult, sixrealmodelghostsegments, actualUI
results, recordedmetrics table; automatedkeyboardvsrecordedmodel labels explicit.
No simulatedtime acceleration orfakewin. PlaywrightvideoWebMoutput under video/;
smoketested encoder works. CanDocker-converttoMP4 ifneeded,nohostglobalinstall.
Needfinishremainingmodelcases, verifyownedmodelcleanup, rebuildfrontendimagefor
newupstreamUIonlyAFTERinferencejobsfinish (avoidproxyrestartinterruptingdriver),
thenrecord. Do not confuse scriptedreference5orders with modelperformance.

## Qwen complete; 4/6 evidence cases verified

Recovered Qwen race remains explicit interruptedpartial:30actualmodelactions,
6racingseconds,2.86%snapshotprogress,0gates. Cookingcook-c9432b614ae84751ad7b
completed full90sshift,90validactions,485.643sinference,0orders2expired0burn/waste/
incorrect. Replay+realghostUIpassed. Manifest/capture now4/6, not full acceptance.
Qwenownedunloaded; ONLYBonsailoaded,race-b951b5e6ee0947a582ce nowactive, then cooking.
Runbook generated docs/plans/2026-11-25-gameplay-demo-runbook.md fromactualreplays;
refresh generator afterall6cases. No score fabricated, no reasoning experiments.

## Bonsai racing complete; final cooking case active

race-b951b5e6ee0947a582ce:51validactions,10.2simracingseconds,
603.33sinference; wallbudgetpartial/provider_error,no validlap. Replayandactual
modelghostUIpassed. Evidence5/6,ghostcaptures5/6. Current/lastmodeljob:
Bonsaicook-4fec17cfc4ce4ec09f19. Driverstillsequential,oneownedBonsaiinstance.
Afterterminal verify6outcomes/captures andactualownedunload, frontendrefreshONLY
(noactiveworker/runtimejobs), video/script capture, inspectrepresentativeframes,
produceplayableartifact+runbook. No newfeaturework needed at this stage.

## All six model cases and ghost checks complete

Bonsai finalcook-4fec17cfc4ce4ec09f19:65validactions,65simshiftseconds
(68inclcountdown),602.803sinference,wallbudgetinconclusivepartial. Replayverified
andactualBonsaicookingghostUIpassed. Manifest6/6; browsercapture6/6allpassed.
DriverfinallyunloadedownedBonsai; LMStudioinventoryverified0loadedinstances,
ownershipJSONnull. No modeljobs/pauseddriverleft. Userpersonalcontrolsconfirmed.

RefreshingONLYfrontendimage/service fornewupstreamUI afterinferenceended; no
worker/runtime/session interruption. Finalvideo recording started fromguardedscript,
logdata/tmp/gameplay-demo/video-recording.log; no claimvideocompleteuntilfile/receipt
verified andrepresentativeframesinspected. Runbook regenerated from6actualrecords.
Finalqualifiedperformance: no modelracinglap; cookingGemma/Qwenfullshift0orders,
Bonsaipartialsnapshot0orders (seeexactmanifest); validity/ghostfunctionality not
claimedastaskwins. Allcurrentpolicyv2/resourceboundsrecorded.


## Final checkpoint — local evidence video ready

Both games remain available at http://localhost:18081/gameplay/racing and
http://localhost:18081/gameplay/cooking. The user personally confirmed that both
controls work. Final Docker inspection found API, frontend, Redis and runtime
healthy, with the worker running. Runtime reports zero active sessions; the final
LM Studio inventory contains no loaded model instances. Sequential evidence jobs
and video capture are finished; no inference is required to challenge recordings.

All six actual model/game recordings verified, and all six corresponding browser
ghost checks passed. No model completed a racing lap or delivered a cooking order.
The Qwen racing result remains an explicitly recovered interrupted partial. The
video separately labels automated keyboard cooking, which delivered one order;
it does not present that controller as a model or as personal user footage.

Shareable H.264 video:
`data/tmp/gameplay-demo/video/gameplay-evidence-2026-11-25.mp4`
(228.44 seconds, 5,932,052 bytes, 1440×1100). Full decode/conversion passed;
representative human, racing ghost, cooking ghost and results frames were visually
inspected. Video receipt records chapters, zero JavaScript errors, provenance and
SHA-256 checksums. Original WebM is retained beside the MP4.

Runbook: `docs/plans/2026-11-25-gameplay-demo-runbook.md`.
Evidence: `data/tmp/gameplay-demo/model-evidence.json`,
`data/tmp/gameplay-demo/ghost-browser-evidence.json`,
`data/tmp/gameplay-demo/video/video-receipt.json`.
Final health/inventory: `data/tmp/gameplay-demo/final-runtime-health.json` and
`data/tmp/gameplay-demo/final-model-inventory.json`.

Final source gate remains 3,535 Python passed / 5 skipped, 33 Node tests,
types/security/sync/Docker production build passed. Subsequent edits only saved
documentation and evidence receipts; final `git diff --check` passed.

Leave the isolated demo stack running. Next gate is user video review and their
discussion with Marcos. No new commits, pushes, branches, PRs, merges or messages
to Marcos were made. HEAD is still 6e52a1f on codex/demo-gameplay; upstream updates
are uncommitted working-tree changes, not a fast-forward. Preserve the clean-base
reconciliation caveat above before any future submission. Do not reopen the live
SQLite database from the host; use API/container access or stop API+worker first.


## Paused November 25 — shutdown and tomorrow's restart

The user requested pausing until tomorrow. All five gameplay-demo containers
(API, worker, frontend, Redis, game-runtime) and their project network were
removed gracefully with compose down, WITHOUT deleting volumes, data or images.
Ports 18081, 18009 and 18080 have no listeners. No task-owned inference drivers,
capture helpers or Playwright browsers remain. Docker had no unrelated running
containers; Docker Desktop was then requested to stop gracefully.

LM Studio was intentionally left open: a newly loaded qwen/qwen3.5-9b instance
(context 40960, parallel 4) is not owned by this demo. Our ownership file is null;
our completed runs used different bounded settings and had already unloaded.
Do not unload or quit that user session without further instruction. Personal
browser windows and Codex were left alone.

All six verified recordings, six ghost checks, database, images, video and
uncommitted source/docs remain intact. User confirmed both controls work.
Next gate remains video review and discussion with Marcos, before publishing.
No development, commits, pushes, branches or PRs were performed during shutdown.

A durable copy of the temporary Compose overlay is now stored at
`data/tmp/gameplay-demo/compose.demo.yml`. To restart tomorrow, start Docker
Desktop, wait for its engine, then run from this repository:

```sh
HARNESS_LAB_LOCAL_SOURCES_DIR="$PWD" HARNESS_LAB_AUTH_DIR=/tmp/gameplay-demo-unused-auth docker compose -p gameplay-demo -f docker-compose.yml -f data/tmp/gameplay-demo/compose.demo.yml --profile prod up -d --no-build api worker frontend-prod
```

The existing images launch both games and their dependencies. Verify health
before reopening http://localhost:18081/gameplay/racing and
http://localhost:18081/gameplay/cooking. Recorded ghosts need no loaded model.
For future deliberate inference, first inspect LM Studio's user-owned models;
do not load another concurrently. Never open the live SQLite database on host.
Video: `data/tmp/gameplay-demo/video/gameplay-evidence-2026-11-25.mp4`.

Final shutdown verification: Docker Desktop exited (no app/backend processes);
all three demo ports are closed; task-owned model ownership is null.

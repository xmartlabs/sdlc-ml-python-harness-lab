# Racing-only data comparison — approved November 26

Local-only execution; cooking files and old evidence preserved, cooking removed
from delivery navigation. Vision, coins, obstacles and visual redesign deferred.
No new branches, commits, pushes, PRs or messages to Marcos.

## Frozen shared trial

Same original Tidal Ring track and physics, seed7,60Hz,30ticks/action (0.5s),
3s fixed countdown,60s racing horizon,60s decision timeout,1800s wall limit.
One valid lap or explicitly labeled DNF/inconclusive limit. Scripted calibration
at60ticks failed;30ticks completed22.0167s clean; calibration is never a competitor.
New version racing-data.v1/environment tidal-ring-data-v1 prevents mixing old
12tick recordings. Additive tick-level telemetry wrapper preserves original engine.
Record all5 actual providers, exact settings, requests/tokens/latencies/wall clocks,
correct tick-weighted mean/maxspeed, offtrack duration/share, collisions/resets,
progress and checkpoint/finish. Failure snapshots never masquerade as official scores.

## Ordered execution and validation

1. Backup uncommitted source; restore Docker/API/worker/Redis/runtime/frontend.
2. Read-only inventory and GPT subscription audit (completed, entitlement pending).
3. Add tests then versioned telemetry wrapper, file-backed comparison artifacts/API,
   strict isolated GPT policy, dashboard and simultaneous independent5ghost replay.
4. Calibrate frozen conditions and verify UI/runtime/provider boundaries.
5. Sequential Gemma, Qwen3.5 9B, Bonsai27B, Qwen3.8 27B; then real GPT6.1Sol
   via existing Codex subscription if entitled. One local model at a time; no downloads.
6. Required gates, actual browser5ghost/video/results inspection, leave app running.

Exact installed IDs: google/gemma-4-e4b Q4_K_M; qwen/qwen3.5-9b Q4_K_M;
prism-ml/bonsai-27b2bit; qwen/qwen3.8-27b4bit. All unloaded at initial inventory.
GPT bundled ID gpt-6.1-sol, low reasoning; CLI system wrapper differs from local
OpenAI-compatible API. Do not claim identical provider harness or paid API access.

## Preservation and runtime

Pre-edit persistent backup data/tmp/racing-data-nov26/pre-edit.patch and
pre-edit-untracked.tar.gz. HEAD unchanged6e52a1f on codex/demo-gameplay; earlier
upstream working-tree delta retained. PR65-to-main integration caveat still applies.
Existing isolated gameplay-demo restored at18081frontend/18009API/18080runtime.
Do not host-open liveSQLite. New comparison evidence uses separate atomic JSON
under data/racing-comparison (container /app/data/racing-comparison is mounted
from data/tmp/gameplay-demo/racing-comparison on host). Preserve old6recordings/video.

Remaining: new code/runtime integration, gates, official5attempts, real5ghost video.
No result yet; this document is the approved execution checkpoint, not success evidence.

## Live checkpoint — official attempts started

Docker game-runtime/API/Redis/worker/productionfrontend running. Dashboard:
http://localhost:18081/gameplay/racing/comparison (rendered heading/WebGL checked,
zero browser JS errors). Human solo route remains /gameplay/racing. Cooking
removed from delivery navigation/route, files and old recordings retained.

Runtime calibration passed22.0167s validlap,0collision/offtrack/reset. Exact
engine fingerprint and condition fingerprint stored in session current.json.
Actual GPT subscription smoke returned a valid bounded JSON action with tools
disabled/emptyworkspace. Requested gpt-6.1-sol low accepted; response model ID
not reported. CodexCLI wrapper differs from LMStudio; no identical-output-cap
claim and no paidAPI fallback. Isolated auth /tmp/racing-nov26-codex-auth is
private/ephemeral; NEVER copy credentials into evidence/checkpoints.

Official sequential driver running execsession28883, script
scripts/racing_comparison.py, logdata/tmp/racing-data-nov26/official-attempts.log.
Authoritative PID/ownedmodel: data/tmp/gameplay-demo/racing-comparison/driver.pid
and owned-model.json. Current manifest, per-attempt journals and finalizedreplays
under data/tmp/gameplay-demo/racing-comparison; container sees same directory
as /app/data/racing-comparison. API is read-only, file writes atomic. No SQLite
access in comparisondriver. Run order Gemma,Qwen3.5,Bonsai,Qwen3.8,GPT.

FullmakeciDockerfrontend running execsession4195, logdata/tmp/racing-data-nov26/ci.log.
Fixturevalidation: Codexpolicy15tests+comparison3tests,8newtelemetry/ghosttests
passed; gameplaylocalsocket tests need escalatednetwork (included in fullgate).
Gate/backendreview fixed walltimeoutlabel,terminalmanifeststate,unknown token
counts remain null. No silent retries or per-model rules changed.

Monitor cheap current.json/log/status untilterminal; do notstartduplicatedriver
orunloaduser-ownedmodels. Driver abort preserves interruptedjournal; explicit
recovery needed, do notrerun completedrecords. Cancellation sentinel session/cancel.
No model results yet at this checkpoint. Remaining: complete5actualattempts,
verifyall5replays/ghosts/pause/restart,renderactualcomparisonvideo,inspectframes,
finalgateevidence/runbook andleaveappavailable. User evidence review gatespublishing.
Usage last40%fivehour,66%weekly (recheck); no reset/purchase.

## Validation milestone

Full `make ci FRONTEND_IN_DOCKER=1` passed on the current source tree (log
data/tmp/racing-data-nov26/ci.log), including new telemetry/ghost/Codexboundary
tests and productiontype/build/security/sync gates. Dashboard rendering was
visually inspected at1440px with no page errors; initial waiting state is honest
(no placeholder cars). Official Gemma now makes valid decisions; pending5run
completion and actual simultaneousghost/video evidence. Do not infer wins from
infrastructurepasses. Configcontext4096 requested, batch128; serverreportsparallel4
despiteparallel1request, captured in provenance (onlyoneMODELloaded).

## Preservation milestone — 52% five-hour allowance consumed

Officialdriver PID93290, execsession28883 remains
active. Only owned model at checkpoint: ["qwen/qwen3.5-9b", "qwen/qwen3.5-9b"].
Authoritative live manifest and fsynced perattemptjournals under
data/tmp/gameplay-demo/racing-comparison; do not duplicate or silentlyrerun.
Frozen fingerprint 492511ee2d8af97e6cf6d5b5e949c04765f8da42e5c9a803e488f688f38ec8a0.
Log data/tmp/racing-data-nov26/official-attempts.log; authdirectory private /tmp
only. Owned cleanup in driverfinally; no old sessions assumed.

Fullgate3553Pythonpassed5skipped and41Node/runtime/coretests (33prior+8new),
Dockerfrontend/type/security/sync passed; a subsequent frontend-only visible
color-key/nonoverlapping-number improvement is building (execsession16681).
No condition/physics/policy changes during trials. Final screenshot/video will
verify rebuilt UI. Recorder script prepared at
data/tmp/racing-data-nov26/record-comparison.mjs, refuses filming until allfive
actual finalized+verified records exist. Do not fabricate or relabel fifthghost.
If GPT subscription quota blocks its realattempt, keep realfailure/partial
recording and explicit state; never switch model or APIbilling. Do not use resetcredits.

## Three verified outcomes — Bonsai completed a real lap

Gemma:120validactions,60sDNF,7.3355%progress,0gates.
Qwen3.5:120validactions,60sDNF,2.8847%progress,0gates,918.657swall.
Bonsai:REALverifiedfinish16.083333s,12gates,0collision/offtrack/reset,33valid
actions,375.582swall,max21m/s,tickmean18.518m/s. No calibration substituted.
Allthreefinalized+replayverified; original journals/actions retained.
Only Qwen3.8 next ownedloaded; then queuedactualGPT. Currentmanifestauthoritative.
Fiveghostvideo stillPENDING. CheckallowancebeforeGPTstage; no new trial or tuning.

## External load blocker and GPT preflight lifecycle

Sequentialdriver28883 exited after3verifiedoutcomes. Qwen3.8 failedLOAD before
anygame/inference: LMStudiooMLXreports15.70GBmodelbutdynamicceiling12.05GB
(2026-11-26serverlog13:12:47). No loadedinstances survived; ownershipnull. User
askedtofree4–5GBunusedappRAM; no system/guardchanges permitted or performed.
EntryQwen3.8 explicitlyblocked/notstarted; no empty/fakeghostrecording.

GPTremainswaiting: separatepreflightconstructor rejected CLI-generated skills/
pluginsdirs fromearliersmoke initsTASKtemporaryCODEX_HOME. No officialGPTaction
requested. Boundedfix: isolateauth-onlycopyPERREQUEST beforefirstofficialattempt,
preservingexactgameprompt/schema/model/effort/tools. Freshauth-onlysource staged
/tmp/racing-nov26-gpt-auth; credentialsNEVERinsource/evidence. Newpreflight
logdata/tmp/racing-data-nov26/gpt-attempt.log retainsValueError, notmodelperformance.
Comparisonstateblockeduntilfix/launch. PriorPIDremoved; do notassumeoldjobactive.
ContinueGPTindependentlythenQwen3.8ONLYafteravailableRAM; no modelattemptretries.

## Four verified outcomes — GPT finished; Qwen3.8 still queued

Actual requestedgpt-6.1-sol low subscriptionattempt finalized+verified:15.533333s
validlap,12gates,0collision/offtrack/reset,32validactions,240.142swall.
ResponseeffectiveIDnotreported,requestedCLI IDaccepted. Codexsystemwrapper
differentfromLMStudio documented; no source/tools/paidAPI fallback.
Lifecyclefix17fixturetests+ruff/mypy/banditpassed; fresh auth-onlyCODEX_HOME
PERREQUEST preventsCLI-generatedskills/plugins leakage; refreshedauthreturned
onlytotaskprivateauthsource. No personal/globalauth/configchanged.
GPTexecsession2387 finished. Noactiveinference/driverPID; modelownershipnull.
Currentcomparison4actualverifiedrecordings,2finishers(Bonsai16.083333s,GPT15.533333s),
2DNFs(Gemma7.3355%,Qwen3.52.8847%). Qwen3.8 hasNORECORDING andisblockedBEFORELOAD
by15.70GBmodelvs12.05GBavailableceiling. UserfreeRAMquestionpending.
Needfifthrecordingandactual5ghostvideo; do notclaimfull5-comparisoncomplete.
Allfrozenconditionsunchanged. NewGPTlaunchhelper data/tmp/racing-data-nov26/tools/run-gpt.py
wasnever-startedattemptcontinuation,notmodelretry. Officialloggpt-official.log.

## Final resumption checkpoint — four actual ghosts, fifth external RAM blocker

Latestfinal `make ci FRONTEND_IN_DOCKER=1` passed3555Python/5skipped,41Node,
alltypes/security/sync/Dockerproductionbuild. Log ci-final.log. Currentfrontend
source excludesblocked-unstartedentries fromreplayfetch (no pointless404).
Actualfourghostbrowsercheck passed aftersettlingDOM beforepauseassertion;
initialassertion was aDOMpainttimingrace, not advancingphysicsafterpause.
Previewrecording itself verifiedpause/restart and0JSErrors. No activeinference;
no loadedmodels; runtime0sessions; driverPIDabsent/ownedmodelnull. Dockerapp
services stayrunning foruser. OriginalNov25MP4preserved5932052bytes.

UserRAMquestionpending. Readonlypsutilcheck24GiBtotal/12.67GiBavailable,
soNOretryofQwen3.8loadmade. No systemmemoryguardchanges. Needusercloseunused
appsfree4–5GBthenrecheckactualheadroom beforeONCE loadingunderidenticalsettings.

Afterheadroomconfirmed, fromrepositoryroot runONLYqueuednever-startedcandidate:
`PYTHONPATH="$PWD:$PWD/src" .venv/bin/python -u data/tmp/racing-data-nov26/tools/run-queued-qwen38.py > data/tmp/racing-data-nov26/qwen38-official.log 2>&1`
Helper refusesanexistingattemptdirectory/activePID/loadedmodel, keepsfrozen
condition, savesownership andfinallyunloadsONLYowninstance. Neverrerunotherfour.
Thenrun actual5ghostvideo script:
`/Applications/ChatGPT.app/Contents/Resources/node data/tmp/racing-data-nov26/record-comparison.mjs`
Originalscriptrefusesincomplete5records. No fifthghost fabricated.

4-of-5PREVIEW captured separately underdata/tmp/racing-data-nov26/video-preview/;
video/receipt/screenshots explicitlymissingQwen3.8 andshowBonsai/GPTactualfinish.
This isNOT completionofrequested5ghostvideo. Visuallyinspectedactual4ghostframe,
colorlegend+numberlabels+resultsreadable. MP4conversion disposablecontainer
execsession37355 pendingverification atthiswrite, previewbrowser14953finished.
Results/runbook docs/plans/2026-11-26-racing-data-results.md.
Quota87%fivehour/73%weekly atGPTfinish; conservefinalcheckpoint, no resetcredits.
No newcommits/pushes/branches/PRs, no contactMarcos. Allchangesremainlocal.

Preview MP4 ready: `data/tmp/racing-data-nov26/video-preview/racing-four-models-preview-2026-11-26.mp4`.
H.264 conversion/full decode passed; media-probe+SHA256 saved inpreviewreceipt.
Video showsFOURactualindependentghosts,clearlymarked4of5/incomplete.
Capture/conversionfinished,noownedhelpersremain; dashboardservicesleftavailable.

## User-authorized remote checkpoint

User requested a checkpoint branch/commit/push of all project work on November26,
without a PR. Branch codex/gameplay-checkpoint-2026-11-26 is based on6e52a1f,
preserving upstream working-tree patches, deferred cooking implementation and
racing comparison together. This is a recovery checkpoint, NOT a clean-main or
review-ready feature submission. Known PR65 ghost-to-main integration caveat
above remains unresolved; no merge/history rewrite is part of this checkpoint.

Four verified outcomes remain Bonsai16.083333s lap, requestedGPT6.1Sol15.533333s
lap, Gemma60sDNF7.3355%,Qwen3.560sDNF2.8847%. Qwen3.8 unstarted/memory-blocked.
Finalgate3555Python/5skip+41Node/types/security/sync/Dockerpassed.
Source/tests/docs/config included; personalCONTRIBUTION_IDEAS.local.md excluded.
Generated model recordings/DB/video/evidence and all auth/.env remain local ignored
files, not force-added: data/tmp/gameplay-demo/racing-comparison and
data/tmp/racing-data-nov26/video-preview/racing-four-models-preview-2026-11-26.mp4.
No PR requested or created. Existing configured developer identity retained.

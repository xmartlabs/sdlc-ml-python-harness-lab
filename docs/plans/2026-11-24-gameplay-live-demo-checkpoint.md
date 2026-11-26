# Local two-game functional demo — November24 afternoon

Branch codex/demo-gameplay based on reviewed ghost head6e52a1f; PR64head597286e
and PR65head6e52a1f remain untouched. No demo commits/pushes/newPRs authorized.
User wants functional racing+cooking, real sequential model text-loop recordings,
human/ghost personal play and video evidence before later polish/submissions.
Prior handoff docs and user CONTRIBUTION_IDEAS.local.md preserved uncommitted.

## Initial launch / evidence boundary

Launching reviewed racing snapshot under /tmp/gameplay-demo-reviewed-src (gitarchive
6e52a1f) to avoid concurrent cooking edits affecting an initial user demo. Compose
project gameplay-demo, overlay /tmp/gameplay-demo-compose.yml. API18009,runtime18080,
frontend18081 loopback only. Data isolated at data/tmp/gameplay-demo mounted/app/data,
DB app/gameplay-demo.db. No sibling-repository or user-auth directory mounts.
Use HARNESS_LAB_AUTH_DIR=/tmp/pr2-auth HARNESS_LAB_LOCAL_SOURCES_DIR="$PWD" with:
`docker compose -p gameplay-demo -f docker-compose.yml -f /tmp/gameplay-demo-compose.yml --profile prod up -d --no-build api worker frontend-prod`
Stop same project via `... --profile prod down` WITHOUT volume/data deletion.
Keep requested demo running at final handoff. Launch health/userURL verification pending.

## Parallel isolated work

- cookingcore agent: pure frontend/src/gameplay/cooking.{mjs,d.mts,test.mjs}.
- cookingUI agent: Cooking page/entry/ghost modules and Vitegame multi-entry config.
- backend agent: CookingEnvironment, narrow shared service/API/tasks/contracts,
  cookingbridge and tests. Root owns runtime game dispatch, RacingEnvironment base,
  mainapp registration, model protocol diagnosis and actual live evidence.

Approved minimum Harbor Kitchen: original soup loop pantry→chop→cook→retrieve→plate→
deliver, bin/waste, burn and deadlines; fixed seed/order schedule. Same human/model
numeric targetstation(-1stay..5)/interact controls, actorauto-walk, 10Hzphysics with
1Hzinput cadence,3scountdown+90sshift. Keyboard1..6selectstation,Space/Einteract,
Escape pause, screencontrols. Independent kitchen ghost, no ingredient/station effect.
Numerical orders/mistakes/expired/burn/waste; model inference clocks separate.
No high-end polish, imported assets, newhostengines, vision or game-creation edits.

## Model proof still pending

Prior Gemma/Qwen invalid, Bonsai timeout; NO model lap. Scripted lap22.43s is not
model evidence. Root will inspect actual replies/response fields/schema/truncation
and fix evidenced protocol issue; Gemma only for internaltests, oneownedloadatatime.
Heavier models only deliberate later sequentialdemo with hostavailable; no downloads.
No silently repaired/fabricated actions, hidden failures or changed scoring clocks.
Last account22%used/78%remaining at review-phase completion; update at milestones.

## Milestone — protocol cause confirmed / both games integrated locally

Running reviewed racing URL http://localhost:18081/gameplay/racing with scripted
reference race-f1aea44fab774ffeb952. This deployment STILL uses archived6e52 source;
working-tree cooking/provider changes are not live until explicit rebuild/remount.
Keep it running for user. IsolatedDB/artifacts and start/stop commands above valid.

Cooking core6tests pass, calibration5correct orders/90s with zero mistakes; UI+ghost
4tests and TS pass; backend13cookingtests/fullgameplay64tests pass. Multi-entry Docker
frontend build passes, runtime registry now supports tidal-ring/harbor-kitchen,
per-game SHA and session controls. Root addedenv bridge/module hooks+HTTPenvironment.
New main cooking route registered; hubcard/finalDocker/liveAPI/UI proofs still pending.
Cooking is not yet personally demonstrated. No demo files committed/pushed.

Exact model diagnosis (only owned Gemma loaded, no othermodels): original full racing
prompt returned emptycontent,512completiontokens of which509reasoning,finish_reason
length (16.03s). JSON-schema alone still empty,253/256reasoningtokens (7.73s).
Adding reasoning_effort=none to structured/v1 request returned VALID132token action,
zero reasoningtokens,finish_reason stop (4.41s). Native reasoning=off also emits an
answer (2.87s), but schema-constrained compat request is chosen for bounded controls.
No action repaired/fabricated; constraints only enforce advertised contract and exact
observationID; actualcontrolvalues selected bymodel. Rawrequest/responses saved
/tmp/demo-gemma-{original-full,structured-full,structured-off,native-off}.json and
summaries /tmp/demo-gemma-{protocol-compare,reasoning-off}.log. Both comparison
processes completed, not stuck. Implementing generic gameplay-only structured/no-
reasoning adapter now, with provenance/finish_reason/usage and invalid-action tests.

Owned model receipt /tmp/demo-owned-model.json: google/gemma-4-e4b,context4096,
evalbatch128. Do not unload user-owned instances; currentlythisGemmaisours.
No Qwen/Bonsai loaded or tested this phase. Needdeliberatehostavailabilityforheavies.
Pendingacceptance: actualGemmaepisodes+usefulghosts, two-gameDockerupgrade, UIhuman/
ghost/save/correctcookproof, finalgate, sequentialall3deliberatedemo anduserpersonal
play/video. Oldmodelemptyresults remain failures; no model lapclaim. Quota52%used/
48%remaining reportedbycoordinator, protectcheckpoint iflimitapproaches.

## Live two-game milestone

BothURLs nowrun CURRENT workingtree API/worker and rebuiltbakedassets:
http://localhost:18081/gameplay/racing and /gameplay/cooking. Overlaynowmountsrepo/app,
separatedata/tmp/gameplay-demo/app/gameplay-demo.db/artifacts retained. Useraskedto
personallytrybothviaasync; awaitinghands-onfeedback. Userexplicitlyapprovedsequential
Qwen/Bonsai afterGemma. Driver/tmp/gameplay-model-demo.py running(session51790),
log/tmp/gameplay-model-demo.log; records+verificationmanifestdata/tmp/gameplay-demo/
model-evidence.json. Exactlyoneownedmodelloadedatonce; fulloriginal600swallbudget,
650swatchdog, nofakeactions. CurrentGemmarace-ea8d1cc7cd044a03b2e5 validactions>50,
2checkpoints/~19%progress; nolapclaim. Then cooking, thenQwen andBonsai eachgame.

Fullmakecheck3526passed5skipped87%; integratedDockerfrontend/typespassed, runtime
7HTTPtests(pass2gameisolation), cookingcore6+ghost4tests. CookingCLIcalib+replayverify
also5orders/90seconds. Actualkeyboardbrowserhand-authoredrecipefulfilled1order;
finished90sshift1expired,0incorrect/waste/burn; savedcook-dae0d75319ea4a4e868c with
scriptedsourcecook-e160f9ca66284e8ea4ab. Ghostpause/restart,serverresultparity,mobile
nooverflow,noJSerrorspassed. Thisautomatedkeyboardtestisnotuserpersonalconfirmation.

Boundedread-onlyreviewfound2P2s nowfixing BEFORE firstmodelcooking: finaltick930
wasskipped(899active ticks vs90s), andCookingWatchwronglyusedstrictghosteligibility
forcountdown-onlyfailures. Core/UIagentsownfixes; rootwillrebuildruntimeandcache
newcookinghash; olderlocalcookingrecordingsremainhistorical/incompatibleasexpected.
Racingcorehashunchanged. Userdemostaysrunning. No commits/pushes, nootherrepos/global
changes. Heavytestingauthorizationrecorded, onlyGemmaloadedsofar.

## Safe between-episode rollout gate

Cookingfinaltickfixed+regression7coretests; Watchzero/countdownfailureplaybackfixed
whileChallengestaysstrict, initial/failedsnapshotvalidationnew; core+ghost13tests.
Correctedcookinghash e40d33910a3df66c8fa01f0fdb73423757b3191831c8a5d485e0e02a8bcffef0.
Needruntimeimageupdate BEFORE modelcooking. Do NOT restartsharedruntimewhileGemma
race active: it would lose the in-memory race session. Only our driver process is
SIGSTOP-paused(pidfile/tmp/gameplay-model-demo.pid); worker finishes its activeGemma
race normally. Builds running /tmp/demo-final-{runtime-build,docker-verify}.log.
Whenracerowterminal, updateonlygame-runtime service (API/workerremainrunning),
regeneratecookingreference,currenthashcheck, then SIGCONT driverPID tolaunchcooking.
Drivercodewaitloopchecksrowterminalbeforewatchdog, so pausedtimewon'tmisclassifydone.
NEVER leaveowneddriverpausedatfinalhandoff. Resumecommand:
`kill -CONT "$(cat /tmp/gameplay-model-demo.pid)"` afterrollout; ownershipPIDverified.
No userprocesses/models interrupted; Gemma race continues anddemouserracingcontinues.

## Quota safeguard — coordinator reports77%used/23%remaining

OwnedGemmainstance google/gemma-4-e4b; /tmp/demo-owned-model.json recordsownership.
DriverPID34703,execsession51790,log/tmp/gameplay-model-demo.log,evidenceJSON
 data/tmp/gameplay-demo/model-evidence.json. No explicit model expiry set; each
modelgameepisode600swallbudget+650swatchdog, driverfinallyunloadsownedinstance.
DrivercurrentlySIGSTOP only tohold cooking untilcurrentrace finishes. Automatic
safe-rollout helper /tmp/gameplay-demo-safe-rollout.py nowrunning, log
/tmp/gameplay-demo-safe-rollout.log; waitsbounded660sforrace-ea8d1cc7cd044a03b2e5
terminal, updatesONLYruntime, verifiescorrectedcookinghash thenSIGCONT34703.
Itmustfinish/resumebeforefinalhandoff; onerrorinspectlog andmanualCONTonlyafter
fixedbuildlive. Do not killdriver orrestartworker/runtimewhileGemmaraceactive.

BothuserURLsremain18081/gameplay/racing and/gameplay/cooking, API/workerfromworking
tree. Livecookingimagecurrentlyolder3c282d.. untilsafe-rollout; correctedimage:demo
builtwith e40d33910a3df66c8fa01f0fdb73423757b3191831c8a5d485e0e02a8bcffef0 andWatchfix.
Usermustrefreshcookingafterrollout; oldlocalcookreferencesnowhistoricalandincompatible.
Modelracing validlegaldecisions>80,2gates (~19%progress); no lap. Actual6modelgame
runsNOTcompleteyet. HeavysequentialQwen/Bonsai explicitlyapprovedbyuserasyncanswer.
Remaining: monitorautomaticrollout andall6driverresults, newbuildcookingcalib/keyboard
proof, realmodelghost browserchecks, finalcheck/noJSerrors, userpersonalconfirmation/
video capture. Do not callrunningjobscomplete. No newPRs/commits/pushes.

## Driver safely resumed / first actual model result

Safe rollout completed: Gemmarace finalized, onlyruntime recreated, correctedcooking
hash e40d339...verified, SIGCONT34703 executed. DriverNO LONGERPAUSED. Gemmacooking
cook-1c862fcf15754556ab0f nowrunning; nextQwen/Bonsai sequential. Logcheckpointgate
/tmp/gameplay-demo-safe-rollout.log. Do not restartworker/runtime duringepisodes.

Gemmarace-ea8d1cc7cd044a03b2e5 FINAL:138validmodelactions,600.229sinference,
30.6secondsincl3scountdown=27.6racingseconds; no validlap,2gates/~19%progress.
Outer600swallbudget stoppedpolicy beforeanothercall; PR1recordsprovider_error with
metrics{}(inconclusive), not a successful lap. Last visible state retained. Replay
verifiedagainstHTTPengine and ghostAPIeligible PARTIAL. Newpolicystructured-action.v2,
reasoningnone/temp0/512max recorded; no repaired/fabricatedactions. Drivermanifest
atdata/tmp/gameplay-demo/model-evidence.json. ActualGemmaghostbrowserproofstarting.
Finalmakecheck3526passed5skipped87%,checksyncpassed; finalDockerverifypassed.

## Near-limit continuation / automatic evidence capture

LATESTACTUALMETER88%used/12%remainingfivehour,43%weekly; no resets/credits.
Finalacceptedcodealluncommittedon codex/demo-gameplay; publishedPRheadsuntouched.
BothdemouserURLs running, rootAPI/workerworkingtreemount; do not switchbranches
or restartworker/runtime duringactiveepisodes. Leave gameplay-demo running.
Fullfinalmakecheck3526passed5skipped87%,syncpassed;7HTTPsessiontests+13racingghost+
13cookingghost=33Nodechecks; finalDockerverifytypes/buildpassed. ReviewP2s fixed.

SAFEGATECOMPLETE(driverNOTpaused): log/tmp/gameplay-demo-safe-rollout.log. Current
Gemmacook-1c862fcf15754556ab0f at~22simsec,raw→choppedvalidmodeldecisions. Sixattempts
areNOTallcomplete. NextQwen/Bonsai authorizedsequential. ModeldriverPID34703,
execsession51790,log/tmp/gameplay-model-demo.log,ownership/tmp/demo-owned-model.json.
Driverfinallyunloadsownedmodel. No explicitmodelexpiry;pergame600wall/650watchdog.
ActualmodelGemmaraceverified138validactions,27.6racetime,2gates19%,partialeligible;
wallbudgetinconclusiveprovider_error,nolap. Currentcookinghashcorrecte40d339... .

ActualGemmahuman-vs-modelghost browserpassed: sourcePARTIAL/realmodelidentity,
independenttranslucentcar, noJSerrors; screenshot/tmp/demo-gemma-racing-ghost.png,
log/tmp/demo-real-model-ghost.log. Corrected-buildkeyboardcooktest rerunpassedone
hand-authoredsouporder/pause/restart/save(1served1expired0wrong/waste/burn,90s),
sourcecook-769797f58ef24bc2b2a7,human-testcook-39d2e120c441451aad9e. Log
/tmp/demo-cooking-keyboard-flow-final.log. Humanpersonalconfirmationstillpending.

AUTOMATICNO-INFERENCEGHOSTCAPTURE running execsession86709:
/tmp/gameplay-capture-model-ghosts.mjs,log/tmp/gameplay-capture-model-ghosts.log.
As eachmodel-evidence entryfinalizes, itopensfreshheadlessbrowser, selectsactual
modelrecording inrealUI, checksidentity/PARTIAL/noJSerrors, savesscreenshotunder
 data/tmp/gameplay-demo/evidence/ and receipt ghost-browser-evidence.json. Stops
once6attemptshandled ordriverends,2hsafetycap; failure flags stayexplicit. This
isnotpersonaluserplayorvideocapture. StopcaptureonlyviaSTOP_CAPTURE file ifneeded;
notamodelprocess. Scriptsbackedupdata/tmp/gameplay-demo/tools/ (ignored,notcommitted).
Dependenciesalreadyavailable/tmp/pr2-playwright andbundledChatGPTnode/BraveCLI.

Checkprogress cheaply: tail /tmp/gameplay-model-demo.log and
/tmp/gameplay-capture-model-ghosts.log; inspectdata/tmp/gameplay-demo/model-evidence.json
andghost-browser-evidence.json. Do NOT duplicatejobs/modelloads. If driverends early,
read traceback/resultandverifyinstanceownership before restarting; no wrongmodel
fallback orfakecompletion. Onall6results, derivesummarylatencies/metricsfromrecordings,
confirmallghostscreenshots anduserpersonalplay,thenusercapturesvideo. Untilthen
stateexplicitlywhichmodelgamesstillrunning/queued/unverified. No morePRsubmissions.

## Gemma evidence complete; Qwen now active

Gemma BOTHgames finalized/replayverified and REALmodelghostbrowserchecksPASSED:
- race-ea8d1cc7cd044a03b2e5:138validactions,partial/no lap,600.229sinference,
  27.6simracingseconds,2gates19%progress; wallbudgetinconclusiveprovider_error.
- cook-1c862fcf15754556ab0f:90validactions,FULL90sshift,313.389sinference,
  0ordersfulfilled,2expired,1burn,0incorrect/waste. Completedshift isNOTtaskwin.
  Metadata/seed/currentengine retained. Zeroorders reflectpoorplay, notprotocolfailure.
Bothghostscreenshotsstored data/tmp/gameplay-demo/evidence/google-gemma-4-e4b-{racing,cooking}-ghost.png;
receiptghost-browser-evidence.jsonpassed2of6. FullJSONmodel-evidence.json contains
results,validactioncounts,provenance,lastvisible state,path+verification flags.

DriverunloadedownedGemma andloaded ONLY qwen/qwen3.5-9b (receipt/tmp/demo-owned-model.json
updated); nowrace-339004fee402475cb1ef running. Qwencooking andBonsaibothgamesremain
pending; do not claimall3models/everyghostverifiedyet. SameboundeddriverPIDs/session/
logsabove; no pausedprocess, no additionalCodexsamplingneededforjobs tocontinue.
Automaticcapturewillpopulateeachfinishedactualmodelghost andexplicitfailureflags.
Remainingsignoff:4modelgames+4browserghostchecks anduserhands-onconfirmation/video.
Everythinglocaluncommitted,noPRpushes; demoLEFT RUNNING withisolatedDB/userdata safe.

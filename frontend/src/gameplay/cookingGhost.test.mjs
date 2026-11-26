import test from 'node:test';
import assert from 'node:assert/strict';
import {COOKING,COOK_TICK_RATE,COOK_ACTION_TICKS,COOK_MAX_TICKS,createKitchen,stepKitchen,observeKitchen,kitchenBaselineControls} from './cooking.mjs';
import {createCookingGhost} from './cookingGhost.mjs';
function recording(count=20) {
 const state=createKitchen(7),initial={state:observeKitchen(state)},frames=[];
 for(let i=0;i<count;i++){const controls=kitchenBaselineControls(state);stepKitchen(state,controls,COOK_ACTION_TICKS);frames.push({action:{controls},ticks_advanced:COOK_ACTION_TICKS,observation:{state:observeKitchen(state)}});}
 return {environment_id:COOKING.id,environment_version:COOKING.version,environment_provenance:{engine_sha256:'build'},config:{seed:7,tick_rate:COOK_TICK_RATE,ticks_per_action:COOK_ACTION_TICKS,max_ticks:COOK_MAX_TICKS},initial,frames};
}
test('independent ghost advances only on shared ticks, freezes at recorded coverage and resets',()=>{
 const replay=recording(),ghost=createCookingGhost(replay,'build',7),human=createKitchen(7),before=structuredClone(human);
 ghost.advanceTo(80);assert.equal(ghost.observation.tick,80);assert.deepEqual(human,before);
 ghost.advanceTo(400);assert.equal(ghost.observation.tick,200);assert.equal(ghost.ended,true);
 const final=structuredClone(ghost.observation);ghost.advanceTo(800);assert.deepEqual(ghost.observation,final);assert.deepEqual(human,before);
 ghost.reset();assert.equal(ghost.observation.tick,0);ghost.advanceTo(80);assert.equal(ghost.observation.tick,80);
});
test('rejects incompatible build, seed, cadence, malformed interval and state changes',()=>{
 for(const mutate of [r=>r.environment_provenance.engine_sha256='other',r=>r.config.seed=8,r=>r.config.tick_rate=20,r=>r.config.ticks_per_action=5,r=>r.config.max_ticks=900,r=>r.frames[5].observation.state.actor.x+=1,r=>r.frames[5].ticks_advanced=9,r=>r.frames[5].action=null]){
  const replay=recording();mutate(replay);assert.throws(()=>createCookingGhost(replay,'build',7));
 }
});
test('rejects countdown-only recordings and actions after a failed decision',()=>{
 assert.throws(()=>createCookingGhost(recording(3),'build',7),/countdown/);
 const replay=recording();replay.frames.splice(5,0,{action:null,ticks_advanced:0,observation:replay.frames[4].observation});assert.throws(()=>createCookingGhost(replay,'build',7),/failed/);
});
test('allows final failed decision without inventing successful work',()=>{
 const replay=recording();replay.frames.push({action:null,ticks_advanced:0,observation:replay.frames.at(-1).observation});const ghost=createCookingGhost(replay,'build',7);
 ghost.advanceTo(930);assert.deepEqual(ghost.observation,replay.frames.at(-2).observation.state);
});

test('Watch permits verified zero-action and countdown-only failures while Challenge rejects them',()=>{
 for(const count of [0,2,3]){
  const replay=recording(count),last=count?replay.frames.at(-1).observation:replay.initial;
  replay.frames.push({action:null,ticks_advanced:0,observation:last});
  assert.throws(()=>createCookingGhost(replay,'build',7),/countdown/);
  const watch=createCookingGhost(replay,'build',7,{forChallenge:false});
  watch.advanceTo(930);assert.equal(watch.coverage,count*10);assert.equal(watch.ended,true);
  assert.deepEqual(watch.observation,last.state);
 }
});
test('Watch still validates initial and failed-frame state even without accepted actions',()=>{
 const replay=recording(0);replay.initial.state.actor.x=99;
 assert.throws(()=>createCookingGhost(replay,'build',7,{forChallenge:false}),/Initial/);
 const failed=recording(0);failed.frames.push({action:null,ticks_advanced:0,observation:{state:{}}});
 assert.throws(()=>createCookingGhost(failed,'build',7,{forChallenge:false}),/state verification/);
});

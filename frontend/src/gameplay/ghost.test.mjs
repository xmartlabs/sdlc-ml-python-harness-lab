import test from 'node:test';
import assert from 'node:assert/strict';
import {createRace,stepRace,observeRace,baselineControls,TRACK} from './racing.mjs';
import {createGhost} from './ghost.mjs';
function recording() {
 const state=createRace(7),frames=[];
 for(let i=0;i<30;i++){const controls=baselineControls(state);stepRace(state,controls,12);frames.push({action:{controls},ticks_advanced:12,observation:{state:observeRace(state)}});}
 return {environment_id:TRACK.id,environment_version:TRACK.version,environment_provenance:{engine_sha256:'build'},config:{seed:7,tick_rate:60,ticks_per_action:12,max_ticks:7200},frames};
}
test('ghost follows the shared simulation clock, resets, and never mutates a human car',()=>{
 const replay=recording(),ghost=createGhost(replay,'build',7),human=createRace(7);
 ghost.advanceTo(240);assert.equal(ghost.observation.tick,240);const frozen=JSON.stringify(human);
 ghost.advanceTo(360);assert.equal(JSON.stringify(human),frozen);assert.equal(ghost.ended,true);
 const last=ghost.observation;ghost.advanceTo(500);assert.deepEqual(ghost.observation,last);
 ghost.reset();assert.equal(ghost.observation.tick,0);ghost.advanceTo(240);assert.equal(ghost.observation.tick,240);
});
test('rejects wrong build, seed, cadence, missing actions and corrupt recorded state',()=>{
 for(const mutate of [r=>r.environment_provenance.engine_sha256='other',r=>r.config.seed=8,r=>r.config.tick_rate=30,r=>r.config.ticks_per_action=6,r=>r.frames[20].observation.state.x+=1,r=>r.frames[20].action=null]){
  const r=recording();mutate(r);assert.throws(()=>createGhost(r,'build',7));
 }
});
test('countdown-only/invalid attempts are not meaningful racing ghosts',()=>{
 const r=recording();r.frames=r.frames.slice(0,15);assert.throws(()=>createGhost(r,'build',7),/movement/);
});

test('allows only floating JSON round-trip drift, not meaningful state changes',()=>{
 const r=recording();r.frames[20].observation.state.upcoming_turn+=1e-14;
 assert.doesNotThrow(()=>createGhost(r,'build',7));
 r.frames[20].observation.state.upcoming_turn+=.0001;assert.throws(()=>createGhost(r,'build',7));
});

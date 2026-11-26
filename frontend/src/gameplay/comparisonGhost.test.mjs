import test from 'node:test';
import assert from 'node:assert/strict';
import {createRace,stepRace,observeRace,baselineControls} from './racing.mjs';
import {createComparisonGhost,COMPARISON_COLORS} from './comparisonGhost.mjs';
const condition={environment_id:'tidal-ring-data-v1',environment_version:'1',engine_sha256:'benchmark-build',seed:7,tick_rate:60,ticks_per_action:60,max_ticks:7200};
function recording(count=8){
 const state=createRace(7),frames=[];
 for(let i=0;i<count;i++){const controls=baselineControls(state);stepRace(state,controls,60);frames.push({action:{controls},ticks_advanced:60,observation:{state:{...observeRace(state),benchmark_extra:99}}});}
 return {environment_id:condition.environment_id,environment_version:'1',environment_provenance:{engine_sha256:'benchmark-build'},config:{...condition},frames};
}
test('five independent ghosts share simulation time, freeze partials and rewind explicitly',()=>{
 const replays=[4,5,6,7,8].map(recording),ghosts=replays.map(r=>createComparisonGhost(r,condition));
 const human=createRace(7),untouched=structuredClone(human);
 ghosts.forEach(g=>g.advanceTo(420));assert.deepEqual(human,untouched);
 assert.equal(ghosts[0].observation.tick,240);assert.equal(ghosts[4].observation.tick,420);
 const frozen=ghosts[0].observation;ghosts.forEach(g=>g.advanceTo(600));assert.deepEqual(ghosts[0].observation,frozen);
 ghosts.forEach(g=>g.reset());assert.ok(ghosts.every(g=>g.observation.tick===0));
 ghosts.forEach(g=>g.advanceTo(300));assert.throws(()=>ghosts[4].advanceTo(2),/Restart/);
 assert.equal(new Set(COMPARISON_COLORS).size,5);
});
test('rejects mismatched comparison parameters and corrupted recorded physics',()=>{
 for(const change of [r=>r.config.seed++,r=>r.config.ticks_per_action=12,r=>r.config.max_ticks=3600,r=>r.environment_provenance.engine_sha256='old',r=>r.frames[4].observation.state.x+=.01,r=>r.frames[4].action.controls.throttle=2]){
  const r=recording();change(r);assert.throws(()=>createComparisonGhost(r,condition));
 }
});
test('zero-action provider failures remain honest stationary recordings',()=>{
 const r=recording(0);r.frames=[{action:null,ticks_advanced:0,outcome:'provider_error'}];
 const ghost=createComparisonGhost(r,condition);ghost.advanceTo(7200);assert.equal(ghost.observation.tick,0);assert.equal(ghost.ended,true);
});

test('wrapper finish at an exact sub-interval tick replays without physics drift',async()=>{
 const benchmark=await import('./racingBenchmark.mjs');
 const state=benchmark.createRace(7),frames=[];
 const config={...condition,ticks_per_action:30};
 while(!benchmark.isTerminal(state)){
  const controls=benchmark.baselineControls(state);benchmark.stepRace(state,controls,30);
  frames.push({action:{controls},ticks_advanced:30,observation:{state:benchmark.observeRace(state)}});
 }
 const replay={environment_id:condition.environment_id,environment_version:'1',environment_provenance:{engine_sha256:'benchmark-build'},config,frames};
 const ghost=createComparisonGhost(replay,config);ghost.advanceTo(7200);
 assert.equal(ghost.observation.phase,'finished');assert.equal(ghost.observation.tick,state.tick);
 assert.equal(ghost.observation.metrics.finish_time_seconds,benchmark.raceMetrics(state).finish_time_seconds);
});

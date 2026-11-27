import test from 'node:test';
import assert from 'node:assert/strict';
import * as race from './racing.mjs';
import * as benchmark from './racingBenchmark.mjs';

test('tick samples measure mean and peak speed rather than averaging decisions', () => {
  const state=benchmark.createRace(7);
  benchmark.stepRace(state,{},180);
  let sum=0,peak=0;
  for(let i=0;i<60;i++){
    benchmark.stepRace(state,{throttle:1},1);
    sum+=state.speed;peak=Math.max(peak,state.speed);
  }
  const metrics=benchmark.raceMetrics(state);
  assert.equal(metrics.speed_sample_ticks,60);
  assert.equal(metrics.mean_speed_mps,sum/60);
  assert.equal(metrics.max_speed_mps,peak);
  assert.equal(metrics.off_track_share,0);
  assert.equal(metrics.race_time_seconds,1);
});

test('held-action batching preserves all telemetry and original physics', () => {
  const a=benchmark.createRace(7),b=benchmark.createRace(7),original=race.createRace(7);
  for(const [controls,ticks] of [[{},180],[{throttle:1,steer:.1},90],[{brake:.5},30]]){
    benchmark.stepRace(a,controls,ticks);
    for(let i=0;i<ticks;i++){benchmark.stepRace(b,controls,1);race.stepRace(original,controls,1);}
    assert.deepEqual(a,b);
    for(const key of Object.keys(original))assert.deepEqual(a[key],original[key],key);
  }
});

test('clean calibration at half-second cadence freezes at exact finish tick', () => {
  const state=benchmark.createRace(7);
  while(!benchmark.isTerminal(state))benchmark.stepRace(state,benchmark.baselineControls(state),30);
  const m=benchmark.raceMetrics(state);
  assert.equal(m.finished,1);assert.equal(m.collisions,0);assert.equal(m.off_track_seconds,0);
  assert.equal(state.tick,state.finishTick);
  assert.equal(m.speed_sample_ticks,state.finishTick-180);
  assert.ok(m.max_speed_mps>10);assert.ok(m.mean_speed_mps>5);
  assert.ok(m.mean_absolute_lateral_m>=0);
  const before=structuredClone(state);
  benchmark.stepRace(state,{throttle:1},30);assert.deepEqual(state,before);
});

test('countdown and terminal time do not dilute speed or off-track statistics', () => {
  const state=benchmark.createRace(7);
  benchmark.stepRace(state,{},180);
  assert.equal(benchmark.raceMetrics(state).mean_speed_mps,0);
  state.heading-=Math.PI/2;
  benchmark.stepRace(state,{throttle:1},360);
  const m=benchmark.raceMetrics(state);
  assert.equal(m.speed_sample_ticks,360);
  assert.ok(Math.abs(m.off_track_share-m.off_track_seconds/6)<1e-12);
  assert.ok(m.off_track_share>0);
  assert.ok(m.mean_absolute_lateral_m>0);
  assert.throws(()=>benchmark.stepRace(state,{steer:2},1),/controls/);
  assert.throws(()=>benchmark.stepRace(state,{},NaN),/ticks/);
});

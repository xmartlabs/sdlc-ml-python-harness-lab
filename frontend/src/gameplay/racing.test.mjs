import test from 'node:test';
import assert from 'node:assert/strict';
import { createRace, stepRace, observeRace, raceMetrics, baselineControls, isTerminal, TRACK, TICK_RATE, MAX_TICKS } from './racing.mjs';
const start = () => { const state = createRace(42); stepRace(state, {}, 3*TICK_RATE); return state; };
test('countdown prevents early movement and acceleration/braking are effective', () => {
  const state = createRace(42); const x = state.x;
  stepRace(state, {throttle:1}, TICK_RATE); assert.equal(state.x,x); assert.equal(observeRace(state).phase,'countdown');
  stepRace(state, {}, 2*TICK_RATE); stepRace(state,{throttle:1},30);
  assert.ok(state.speed > 3); const speed = state.speed; stepRace(state,{brake:1},15); assert.ok(state.speed < speed);
});
test('seed/actions reproduce and held controls are independent of batching', () => {
  const a = start(), b = start(); const controls = {throttle:0.7,steer:0.1};
  stepRace(a,controls,60); for(let i=0;i<60;i++) stepRace(b,controls,1);
  assert.deepEqual(a,b); assert.deepEqual(createRace(42),createRace(42));
  assert.throws(()=>stepRace(a,{steer:2},1),/controls/); assert.throws(()=>stepRace(a,{},1.5),/ticks/);
});
test('reset never advances checkpoints; repeated held reset counts once', () => {
  const state = start(); stepRace(state,{throttle:1},30); const before = state.checkpointsPassed;
  stepRace(state,{reset:1},12); assert.equal(state.checkpointsPassed,before); assert.equal(raceMetrics(state).resets,1);
  assert.equal(state.speed,0); stepRace(state,{},1); stepRace(state,{reset:1},1); assert.equal(raceMetrics(state).resets,2);
});
test('wrong-way crossing and teleporting ahead cannot award checkpoints', () => {
  const state = start(); state.heading += Math.PI; stepRace(state,{throttle:1},100);
  assert.equal(state.checkpointsPassed,0);
  const target = TRACK.checkpoints[5]; state.x=target.x; state.z=target.z; state.heading=target.heading;
  stepRace(state,{throttle:1},2); assert.equal(state.checkpointsPassed,0);
});
test('barrier contact is counted per impact, rather than per simulation frame', () => {
  const state = start(); state.heading -= Math.PI/2;
  stepRace(state,{throttle:1},180); assert.ok(raceMetrics(state).collisions >=1);
  const before=state.collisions; stepRace(state,{throttle:1},10); assert.ok(state.collisions<=before+1);
  assert.ok(raceMetrics(state).off_track_seconds>0);
});
test('calibration controller completes ordered lap without collisions and records numeric result', () => {
  const state = createRace(7);
  while(!isTerminal(state)) stepRace(state,baselineControls(state),12);
  const metrics = raceMetrics(state);
  assert.equal(state.phase,'finished'); assert.equal(state.checkpointsPassed,TRACK.checkpoints.length);
  assert.equal(metrics.progress,1); assert.equal(metrics.collisions,0); assert.ok(metrics.finish_time_seconds>15); assert.ok(metrics.finish_time_seconds<80);
  const before=structuredClone(state); stepRace(state,{throttle:1},12); assert.deepEqual(state,before);
});
test('time limit yields finite incomplete metrics without a fabricated finish time', () => {
  const state=createRace(1); stepRace(state,{},MAX_TICKS);
  assert.equal(state.phase,'timeout'); assert.ok(isTerminal(state)); assert.equal(raceMetrics(state).progress,0);
  assert.equal('finish_time_seconds' in raceMetrics(state),false);
  assert.ok(Object.values(raceMetrics(state)).every(Number.isFinite));
});

test('reset follows finite numeric gameplay controls and threshold', () => {
  const state = start();
  assert.throws(() => stepRace(state, {reset:true}, 1), /controls/);
  assert.throws(() => stepRace(state, {reset:NaN}, 1), /controls/);
  stepRace(state, {reset:0.49}, 1); assert.equal(state.resets,0);
  stepRace(state, {reset:0.5}, 1); assert.equal(state.resets,1);
});
test('driving backward across start and returning does not manufacture forward distance', () => {
  const state = start(); state.speed=6; state.heading+=Math.PI;
  const controls={throttle:1.01/9};
  stepRace(state, controls, 12); state.heading+=Math.PI;
  stepRace(state, controls, 12);
  assert.ok(Math.abs(state.progressDistance)<0.05, String(state.progressDistance));
  assert.equal(state.checkpointsPassed,0);
});

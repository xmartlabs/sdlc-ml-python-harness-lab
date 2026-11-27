import assert from 'node:assert/strict';
import test from 'node:test';
import * as engine from './racing.mjs';
import { CONDITION, replay } from '../../../benchmarks/racing-play-v1/starter/condition.mjs';
import { createRunReplay } from './runReplay.mjs';

function baselineEpisode(limit) {
  const state = engine.createRace(CONDITION.seed);
  engine.stepRace(state, {}, CONDITION.countdown_ticks);
  const controls = [];
  while (controls.length < limit && !engine.isTerminal(state) && state.tick < CONDITION.horizon_ticks) {
    const c = engine.baselineControls(state);
    controls.push(c);
    engine.stepRace(state, c, CONDITION.ticks_per_action);
  }
  return controls;
}

test('the watched lap ends exactly where the graded lap ended', () => {
  const controls = baselineEpisode(200);
  const graded = replay(engine, controls).state;
  const watched = createRunReplay(CONDITION, controls);
  while (!watched.done()) watched.advance(7);
  assert.equal(watched.state.phase, 'finished');
  assert.deepEqual(engine.raceMetrics(watched.state), engine.raceMetrics(graded));
  assert.equal(watched.state.x, graded.x);
});

test('an unfinished episode stops after its last action, not at the horizon', () => {
  const controls = baselineEpisode(5);
  const watched = createRunReplay(CONDITION, controls);
  while (!watched.done()) watched.advance(60);
  assert.equal(watched.state.tick, CONDITION.countdown_ticks + 5 * CONDITION.ticks_per_action);
  assert.deepEqual(engine.raceMetrics(watched.state), engine.raceMetrics(replay(engine, controls).state));
});

test('the countdown runs on zero controls before the first action', () => {
  const watched = createRunReplay(CONDITION, [{ throttle: 1, brake: 0, steer: 0, reset: 0 }]);
  assert.equal(watched.actionIndex(), -1);
  watched.advance(CONDITION.countdown_ticks);
  assert.equal(watched.state.speed, 0);
  assert.equal(watched.actionIndex(), 0);
});

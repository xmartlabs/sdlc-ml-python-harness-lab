import test from 'node:test';
import assert from 'node:assert/strict';
import { racingPlaySummary } from './racingPlay.mjs';

const score = (name, fields) => ({ name, version: 'v1', value_num: null, value_bool: null, value_text: null, reason: '', ...fields });

test('no racing scores gives no summary', () => {
  assert.equal(racingPlaySummary([score('test_pass_rate', { value_num: 1 })]), null);
});

test('a finished played lap shows mode, source and time', () => {
  const summary = racingPlaySummary([
    score('racing_play_mode', { value_text: 'played', reason: 'played · events' }),
    score('racing_progress', { value_num: 1 }),
    score('racing_finish_seconds', { value_num: 15.5333 }),
    score('racing_collisions', { value_num: 0 }),
    score('racing_resets', { value_num: 0 }),
  ]);
  assert.deepEqual(summary, { mode: 'played', badge: 'Played · events', outcome: 'Lap 15.53 s', impacts: '0 collisions · 0 resets', complete: true });
});

test('a scripted unfinished run shows progress', () => {
  const summary = racingPlaySummary([
    score('racing_play_mode', { value_text: 'scripted', reason: 'scripted · timing' }),
    score('racing_progress', { value_num: 0.0734 }),
    score('racing_collisions', { value_num: 1 }),
    score('racing_resets', { value_num: 0 }),
    score('racing_episode_complete', { value_bool: false }),
  ]);
  assert.equal(summary.badge, 'Scripted · timing');
  assert.equal(summary.outcome, 'No lap · 7.3% of the lap');
  assert.equal(summary.complete, false);
});

test('an unknown mode with no episode', () => {
  const summary = racingPlaySummary([score('racing_play_mode', { value_text: 'unknown', reason: 'unknown · none' })]);
  assert.equal(summary.badge, 'Unknown');
  assert.equal(summary.outcome, 'No valid episode');
});

test('a rewritten log keeps the source word only in the badge', () => {
  const summary = racingPlaySummary([
    score('racing_play_mode', { value_text: 'scripted', reason: 'scripted · events (the action log does not match the recorded calls)' }),
  ]);
  assert.equal(summary.badge, 'Scripted · events');
});

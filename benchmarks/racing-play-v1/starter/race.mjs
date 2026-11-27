#!/usr/bin/env node
/**
 * race — play one lap of the Tidal Ring circuit, one decision at a time.
 *
 *   race rules              the rules of the race
 *   race state              where the car is now
 *   race step '<controls>'  apply controls for 0.5 s of game time
 *
 * Game time only advances on `race step`. Every accepted step is appended to
 * .race/actions.jsonl; the state is rebuilt from that log on every call.
 */
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import * as engine from './racing.mjs';
import {CONDITION, replay, validateControls} from './condition.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const DIR = path.join(HERE, '.race');
const ACTIONS = path.join(DIR, 'actions.jsonl');
const CALLS = path.join(DIR, 'calls.jsonl');

const RULES = `Finish one valid lap of the Tidal Ring circuit, passing all ${12} checkpoints in order.
Each \`race step\` applies your controls for ${CONDITION.ticks_per_action / CONDITION.tick_rate} s of game time;
nothing moves between calls. The race lasts at most ${(CONDITION.horizon_ticks - CONDITION.countdown_ticks) / CONDITION.tick_rate} s of game time.
Controls (all four required): throttle 0..1, brake 0..1, steer -1..1, reset 0..1.
steer has the same sign as heading_error (radians toward a point ahead on the road).
Keep lateral_offset within road_half_width; slow down when upcoming_turn is large.
Hitting a barrier counts a collision and costs speed. reset>=0.5 puts the car back at
the last checkpoint, stopped, without gaining progress (the clock keeps running).
Example: race step '{"throttle":1,"brake":0,"steer":0.2,"reset":0}'`;

function readActions() {
  if (!fs.existsSync(ACTIONS)) return [];
  return fs.readFileSync(ACTIONS, 'utf8').split('\n').filter(Boolean).map((line) => JSON.parse(line).controls);
}

function view(result, actions) {
  return {...engine.observeRace(result.state), terminal: result.terminal, reason: result.reason, actions_taken: actions};
}

function fail(message) {
  process.stderr.write(`race: ${message}\n`);
  process.exit(2);
}

const [command, payload] = process.argv.slice(2);
let actions;
try {
  actions = readActions();
} catch {
  fail('.race/actions.jsonl is not readable; it must hold one JSON object per line');
}

if (command === 'rules') {
  process.stdout.write(RULES + '\n');
} else if (command === 'state') {
  let result;
  try { result = replay(engine, actions); } catch (error) { fail(`recorded episode is invalid: ${error.message}`); }
  process.stdout.write(JSON.stringify(view(result, actions.length)) + '\n');
} else if (command === 'step') {
  let controls;
  try { controls = validateControls(JSON.parse(payload ?? '')); } catch (error) { fail(error instanceof SyntaxError ? 'controls must be valid JSON' : error.message); }
  let before;
  try { before = replay(engine, actions); } catch (error) { fail(`recorded episode is invalid: ${error.message}`); }
  if (before.terminal) fail(`episode is over: ${before.reason}`);
  const after = replay(engine, [...actions, controls]);
  fs.mkdirSync(DIR, {recursive: true});
  const index = actions.length;
  const s = after.state;
  // What the tool showed after this step; the grader checks the replay matches.
  const seen = {tick: s.tick, x: s.x, z: s.z, speed: s.speed, heading: s.heading};
  fs.appendFileSync(ACTIONS, JSON.stringify({i: index, controls, seen}) + '\n');
  fs.appendFileSync(CALLS, JSON.stringify({i: index, t: Date.now()}) + '\n');
  process.stdout.write(JSON.stringify({...view(after, index + 1), events: after.events.at(-1)}) + '\n');
} else {
  fail('usage: race rules | race state | race step \'{"throttle":1,"brake":0,"steer":0,"reset":0}\'');
}

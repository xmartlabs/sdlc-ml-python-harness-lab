/**
 * The frozen racing-play-v1 condition, shared by the `race` tool and the grader.
 *
 * The engine is injected so the grader can replay with the lab's own copy and
 * never the one in the agent's workspace. The engine itself runs for 120 s; this
 * module stops the episode at a 60 s racing horizon without changing it.
 */
export const CONDITION = Object.freeze({
  seed: 7,
  tick_rate: 60,
  ticks_per_action: 30,
  countdown_ticks: 180,
  horizon_ticks: 3780,
  environment: 'tidal-ring',
  version: 'racing-play.v1',
});

const FIELDS = [['throttle', 0, 1], ['brake', 0, 1], ['steer', -1, 1], ['reset', 0, 1]];

/** All four controls, finite numbers within bounds; throws naming the bad field. */
export function validateControls(value) {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('controls must be a JSON object with throttle, brake, steer and reset');
  }
  for (const key of Object.keys(value)) {
    if (!FIELDS.some(([name]) => name === key)) throw new Error(`unknown control "${key}"`);
  }
  const controls = {};
  for (const [name, low, high] of FIELDS) {
    const number = value[name];
    if (typeof number !== 'number' || !Number.isFinite(number)) {
      throw new Error(`control "${name}" must be a finite number`);
    }
    if (number < low || number > high) throw new Error(`control "${name}" must be within ${low}..${high}`);
    controls[name] = number;
  }
  return controls;
}

function terminalReason(engine, state) {
  if (state.phase === 'finished') return 'finished';
  if (state.tick >= CONDITION.horizon_ticks || engine.isTerminal(state)) return 'horizon';
  return null;
}

/** Replay `actions` (controls objects) from the start; returns the final state. */
export function replay(engine, actions) {
  const state = engine.createRace(CONDITION.seed);
  engine.stepRace(state, {throttle: 0, brake: 0, steer: 0, reset: 0}, CONDITION.countdown_ticks);
  const observations = [];
  const events = [];
  for (const controls of actions) {
    if (terminalReason(engine, state)) throw new Error('action after the episode ended');
    const ticks = Math.min(CONDITION.ticks_per_action, CONDITION.horizon_ticks - state.tick);
    events.push(engine.stepRace(state, validateControls(controls), ticks));
    observations.push(engine.observeRace(state));
  }
  const reason = terminalReason(engine, state);
  return {state, observations, events, terminal: reason !== null, reason};
}

/**
 * node replay.mjs <engine> <condition> <actions.jsonl>
 *
 * Replays the agent's action log with the lab's own engine and condition (both
 * passed as absolute paths, never the workspace copies) and prints one JSON
 * object. An invalid log prints {"valid": false, "invalid_reason": ...}.
 */
import fs from 'node:fs';
import {pathToFileURL} from 'node:url';

const [enginePath, conditionPath, actionsPath] = process.argv.slice(2);
const engine = await import(pathToFileURL(enginePath).href);
const {CONDITION, replay} = await import(pathToFileURL(conditionPath).href);

function out(value) {
  process.stdout.write(JSON.stringify(value) + '\n');
}

let actions;
let records;
try {
  const text = fs.existsSync(actionsPath) ? fs.readFileSync(actionsPath, 'utf8') : '';
  records = text.split('\n').filter(Boolean).map((line, n) => {
    const record = JSON.parse(line);
    if (record === null || typeof record !== 'object' || !('controls' in record)) {
      throw new Error(`line ${n + 1} has no controls`);
    }
    return record;
  });
  actions = records.map((record) => record.controls);
} catch (error) {
  out({valid: false, invalid_reason: `action log unreadable: ${error.message}`, actions: 0});
  process.exit(0);
}
if (actions.length === 0) {
  out({valid: false, invalid_reason: 'no actions recorded', actions: 0});
  process.exit(0);
}
try {
  const result = replay(engine, actions);
  // Each line carries what the tool printed after that step. A line edited after
  // the fact replays to a different car; a line with no record was never shown.
  const mismatch = records.findIndex((record, n) => {
    const seen = record.seen;
    const state = result.observations[n];
    if (!seen || typeof seen !== 'object') return true;
    return seen.tick !== state.tick || ['x', 'z', 'speed', 'heading'].some((k) => seen[k] !== state[k]);
  });
  if (mismatch !== -1) {
    out({valid: false, invalid_reason: `action log does not match what the race tool showed (line ${mismatch + 1})`, actions: actions.length});
    process.exit(0);
  }
  out({
    valid: true,
    invalid_reason: '',
    actions: actions.length,
    terminal: result.terminal,
    reason: result.reason,
    ticks: result.state.tick,
    metrics: engine.raceMetrics(result.state),
    checkpoints_total: engine.TRACK.checkpoints.length,
    condition: CONDITION,
    controls: actions,
  });
} catch (error) {
  out({valid: false, invalid_reason: `replay rejected the log: ${error.message}`, actions: actions.length});
}

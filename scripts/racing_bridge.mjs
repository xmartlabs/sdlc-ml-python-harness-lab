// The browser and model recorder execute this exact simulation source.
import { createInterface } from 'node:readline';
import { createRace, stepRace, observeRace, raceMetrics, isTerminal, baselineControls } from '../frontend/src/gameplay/racing.mjs';
let state;
for await (const line of createInterface({input: process.stdin})) {
  try {
    const command = JSON.parse(line);
    let events = [];
    if (command.op === 'reset') state = createRace(command.seed);
    else if (!state) throw new Error('Reset required');
    else if (command.op === 'step') events = stepRace(state, command.controls, command.ticks);
    else if (command.op !== 'baseline') throw new Error('Unknown bridge operation');
    process.stdout.write(JSON.stringify({state: observeRace(state), terminal: isTerminal(state), metrics: raceMetrics(state), events, baseline: command.op === 'baseline' ? baselineControls(state) : null}) + '\n');
  } catch {
    process.stdout.write(JSON.stringify({error: 'Racing simulation request failed'}) + '\n');
  }
}

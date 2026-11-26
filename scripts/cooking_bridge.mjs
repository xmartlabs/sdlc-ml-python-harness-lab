// The browser and model recorder execute this exact simulation source.
import { createInterface } from 'node:readline';
import { createKitchen, stepKitchen, observeKitchen, kitchenMetrics, kitchenTerminal, kitchenBaselineControls } from '../frontend/src/gameplay/cooking.mjs';
let state;
for await (const line of createInterface({input: process.stdin})) {
  try {
    const command = JSON.parse(line);
    let events = [];
    if (command.op === 'reset') state = createKitchen(command.seed);
    else if (!state) throw new Error('Reset required');
    else if (command.op === 'step') events = stepKitchen(state, command.controls, command.ticks);
    else if (command.op !== 'baseline') throw new Error('Unknown bridge operation');
    process.stdout.write(JSON.stringify({state: observeKitchen(state), terminal: kitchenTerminal(state), metrics: kitchenMetrics(state), events, baseline: command.op === 'baseline' ? kitchenBaselineControls(state) : null}) + '\n');
  } catch {
    process.stdout.write(JSON.stringify({error: 'Cooking simulation request failed'}) + '\n');
  }
}

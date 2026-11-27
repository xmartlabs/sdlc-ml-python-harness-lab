import type { RaceControls, RaceObservation, RaceState } from './racing.mjs';

export interface ReplayCondition {
  seed: number; tick_rate: number; ticks_per_action: number;
  countdown_ticks: number; horizon_ticks: number;
}
export interface RunReplay {
  state: RaceState;
  lastTick: number;
  done(): boolean;
  actionIndex(): number;
  advance(ticks: number): string[];
  observe(): RaceObservation;
}
export function createRunReplay(condition: ReplayCondition, controls: RaceControls[]): RunReplay;

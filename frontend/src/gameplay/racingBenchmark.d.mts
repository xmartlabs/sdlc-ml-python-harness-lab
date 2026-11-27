import type {RaceState,RaceControls,RaceMetrics,RaceObservation} from './racing.mjs';
export {TRACK,TICK_RATE,MAX_TICKS,isTerminal,baselineControls} from './racing.mjs';
export const ACTION_TICKS:number;
export const BENCHMARK:Readonly<{id:string;version:string}>;
export interface BenchmarkState extends RaceState {
  telemetry:{ticks:number;speedSum:number;maxSpeed:number;lateralSum:number};
}
export interface BenchmarkMetrics extends RaceMetrics {
  speed_sample_ticks:number;max_speed_mps:number;mean_speed_mps:number;
  off_track_share:number;mean_absolute_lateral_m:number;
}
export interface BenchmarkObservation extends RaceObservation {metrics:BenchmarkMetrics}
export function createRace(seed?:number):BenchmarkState;
export function stepRace(state:BenchmarkState,controls?:RaceControls,ticks?:number):string[];
export function raceMetrics(state:BenchmarkState):BenchmarkMetrics;
export function observeRace(state:BenchmarkState):BenchmarkObservation;

export interface Point { x: number; z: number }
export interface Checkpoint extends Point { heading: number; distance: number }
export interface RaceControls { throttle?: number; brake?: number; steer?: number; reset?: number }
export type RacePhase = 'countdown' | 'racing' | 'finished' | 'timeout';
export interface RaceState extends Point {
  version: string; trackId: string; seed: number; tick: number; phase: RacePhase;
  heading: number; speed: number; checkpointsPassed: number;
  previousDistance: number; progressDistance: number; maxProgressDistance: number;
  collisions: number; offTrackTicks: number; resets: number;
  resetHeld: boolean; collisionHeld: boolean; finishTick: number | null;
}
export interface RaceMetrics {
  finished: number;
  progress: number; checkpoints_passed: number; collisions: number;
  off_track_seconds: number; resets: number; race_time_seconds: number;
  finish_time_seconds?: number;
}
export interface RaceObservation extends Point {
  track_id: string; track_version: string; seed: number; tick: number;
  phase: RacePhase; countdown_seconds: number; game_time_seconds: number;
  heading: number; speed: number; heading_error: number; lateral_offset: number;
  lookahead: Point; upcoming_turn: number; road_half_width: number;
  checkpoints_passed: number; checkpoints_total: number; progress: number;
  metrics: RaceMetrics;
}
export const TRACK: Readonly<{
  id: string; name: string; version: string; width: number; length: number;
  centerline: readonly Readonly<Point>[]; checkpoints: readonly Readonly<Checkpoint>[];
}>;
export const TICK_RATE: number;
export const ACTION_TICKS: number;
export const MAX_TICKS: number;
export function createRace(seed?: number): RaceState;
export function stepRace(state: RaceState, controls?: RaceControls, ticks?: number): string[];
export function observeRace(state: RaceState): RaceObservation;
export function raceMetrics(state: RaceState): RaceMetrics;
export function isTerminal(state: RaceState): boolean;
export function baselineControls(state: RaceState): RaceControls;

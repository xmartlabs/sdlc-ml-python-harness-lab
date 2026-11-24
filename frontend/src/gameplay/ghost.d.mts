import type { RaceObservation } from './racing.mjs';
export interface GhostTimeline {coverage:number; reset():void; advanceTo(tick:number):void; readonly observation:RaceObservation; readonly ended:boolean}
export function createGhost(replay:unknown,engineHash:string,seed:number):GhostTimeline;

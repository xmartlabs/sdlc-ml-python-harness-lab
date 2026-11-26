import type { KitchenObservation } from './cooking.mjs';
export interface CookingGhostTimeline {coverage:number;reset():void;advanceTo(tick:number):void;readonly observation:KitchenObservation;readonly ended:boolean}
export function createCookingGhost(replay:unknown,engineHash:string,seed:number,options?:{forChallenge?:boolean}):CookingGhostTimeline;

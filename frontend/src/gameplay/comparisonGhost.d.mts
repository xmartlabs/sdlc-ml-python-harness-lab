import type {RaceObservation} from './racing.mjs';
export interface ComparisonCondition {
 environment_id:string;environment_version:string;engine_sha256:string;
 seed:number;tick_rate:number;ticks_per_action:number;max_ticks:number;
 [key:string]:unknown;
}
export interface ComparisonGhost {
 coverage:number;reset():void;advanceTo(tick:number):void;
 readonly observation:RaceObservation;readonly ended:boolean;
}
export const COMPARISON_COLORS:readonly string[];
export function createComparisonGhost(replay:unknown,condition:ComparisonCondition):ComparisonGhost;

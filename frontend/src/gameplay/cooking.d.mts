export interface KitchenStation {id:number;name:string;x:number;z:number}
export interface KitchenControls {target:number;interact:number}
export type KitchenPhase='countdown'|'running'|'finished';
export type KitchenHeld='empty'|'raw'|'chopped'|'cooked'|'plated';
export interface KitchenPot {stage:'empty'|'cooking'|'ready'|'burnt';ticks:number}
export interface KitchenOrder {id:number;recipe:string;deadline_tick:number}
export interface KitchenState {
 version:string;seed:number;tick:number;phase:KitchenPhase;actor:{x:number;z:number;target:number};
 held:KitchenHeld;chop_ticks:number;pot:KitchenPot;orders:KitchenOrder[];orderSerial:number;
 ordersFulfilled:number;ordersExpired:number;mistakes:number;waste:number;burns:number;workTicks:number;
}
export interface KitchenMetrics {orders_fulfilled:number;orders_expired:number;incorrect_deliveries:number;food_wasted:number;burns:number;game_time_seconds:number;finished:number}
export interface KitchenObservation {
 environment_id:string;environment_version:string;phase:KitchenPhase;seed:number;tick:number;
 countdown_seconds:number;game_time_seconds:number;remaining_seconds:number;
 actor:{x:number;z:number;target:number};held:KitchenHeld;chop_ticks:number;chop_required_ticks:number;
 pot:KitchenPot;cook_required_ticks:number;burn_after_ticks:number;
 orders:(KitchenOrder&{remaining_seconds:number})[];stations:KitchenStation[];metrics:KitchenMetrics;
}
export const COOKING:Readonly<{id:string;version:string;name:string;stations:readonly Readonly<KitchenStation>[]} >;
export const COOK_TICK_RATE:number;
export const COOK_ACTION_TICKS:number;
export const COOK_MAX_TICKS:number;
export function createKitchen(seed?:number):KitchenState;
export function stepKitchen(state:KitchenState,controls:KitchenControls,ticks?:number):string[];
export function observeKitchen(state:KitchenState):KitchenObservation;
export function kitchenMetrics(state:KitchenState):KitchenMetrics;
export function kitchenTerminal(state:KitchenState):boolean;
export function kitchenBaselineControls(state:KitchenState):KitchenControls;

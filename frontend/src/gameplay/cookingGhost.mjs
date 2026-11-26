/** Recorded independent kitchen: no shared inventory, orders, or human resources. */
import {COOKING,COOK_TICK_RATE,COOK_ACTION_TICKS,COOK_MAX_TICKS,createKitchen,stepKitchen,observeKitchen} from './cooking.mjs';
function sameState(a,b) {
 if(typeof a==='number'&&typeof b==='number')return Number.isFinite(a)&&Number.isFinite(b)&&Math.abs(a-b)<=1e-9;
 if(a&&typeof a==='object'&&b&&typeof b==='object')return Object.keys(a).length===Object.keys(b).length&&Object.keys(a).every(k=>Object.hasOwn(b,k)&&sameState(a[k],b[k]));
 return a===b;
}
export function createCookingGhost(replay,engineHash,seed,{forChallenge=true}={}) {
 if(!replay||replay.environment_id!==COOKING.id||replay.environment_version!==COOKING.version||!engineHash||replay.environment_provenance?.engine_sha256!==engineHash||replay.config?.seed!==seed||replay.config.tick_rate!==COOK_TICK_RATE||replay.config.ticks_per_action!==COOK_ACTION_TICKS||replay.config.max_ticks!==COOK_MAX_TICKS)throw Error('Ghost requires the same kitchen build, seed and control cadence.');
 if(!Array.isArray(replay.frames)||replay.frames.length>COOK_MAX_TICKS/COOK_ACTION_TICKS+1)throw Error('Invalid cooking recording.');
 const intervals=[];let check=createKitchen(seed),coverage=0,failed=false;
 if(!sameState(observeKitchen(check),replay.initial?.state))throw Error('Initial cooking state verification failed.');
 for(const frame of replay.frames){
  if(failed)throw Error('Action after failed decision.');
  if(frame.action===null&&frame.ticks_advanced===0){
   if(!sameState(observeKitchen(check),frame.observation?.state))throw Error('Failed cooking frame state verification failed.');
   failed=true;continue;
  }
  if(!frame.action||frame.ticks_advanced!==COOK_ACTION_TICKS||coverage>=COOK_MAX_TICKS)throw Error('Invalid cooking action interval.');
  const controls=structuredClone(frame.action.controls);stepKitchen(check,controls,COOK_ACTION_TICKS);coverage+=COOK_ACTION_TICKS;
  if(!sameState(observeKitchen(check),frame.observation?.state))throw Error('Cooking ghost state verification failed.');
  intervals.push(controls);
 }
 if(forChallenge&&coverage<=3*COOK_TICK_RATE)throw Error('Recording ends during countdown; no cooking coverage.');
 let state,cursor;
 const ghost={coverage,reset(){state=createKitchen(seed);cursor=0;},advanceTo(tick){
  if(!Number.isInteger(tick)||tick<cursor)throw Error('Reset the ghost before rewinding.');
  while(cursor<Math.min(tick,coverage)){stepKitchen(state,intervals[Math.floor(cursor/COOK_ACTION_TICKS)],1);cursor++;}
 },get observation(){return observeKitchen(state);},get ended(){return cursor>=coverage;}};
 ghost.reset();return ghost;
}

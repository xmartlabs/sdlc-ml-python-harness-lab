/** Independent recorded car: advances only with the human simulation clock. */
import {createRace,stepRace,observeRace,TRACK,TICK_RATE,ACTION_TICKS} from './racing.mjs';
// JSON re-encoding and different JS engines can differ by a few floating ULPs.
// The server still performs authoritative full replay verification. This browser
// check permits only sub-nanometre numerical drift, never missing fields or meaningful state changes.
function sameState(a,b) {
 if(typeof a==='number'&&typeof b==='number')return Number.isFinite(a)&&Number.isFinite(b)&&Math.abs(a-b)<=1e-9;
 if(a&&typeof a==='object'&&b&&typeof b==='object')return Object.keys(a).length===Object.keys(b).length&&Object.keys(a).every(k=>Object.hasOwn(b,k)&&sameState(a[k],b[k]));
 return a===b;
}
export function createGhost(replay,engineHash,seed) {
 if(replay.environment_id!==TRACK.id||replay.environment_version!==TRACK.version||replay.environment_provenance?.engine_sha256!==engineHash||!engineHash||replay.config?.seed!==seed||replay.config.tick_rate!==TICK_RATE||replay.config.ticks_per_action!==ACTION_TICKS||replay.config.max_ticks!==7200)throw Error('Ghost requires the same engine build, track, seed and control cadence.');
 if(!Array.isArray(replay.frames)||replay.frames.length>601)throw Error('Invalid ghost recording.');
 const intervals=[];let check=createRace(seed),coverage=0;
 for(const frame of replay.frames){
  if(frame.action===null&&frame.ticks_advanced===0)continue;
  if(!frame.action||frame.ticks_advanced!==ACTION_TICKS)throw Error('Incomplete or invalid ghost action interval.');
  const controls=structuredClone(frame.action.controls);stepRace(check,controls,ACTION_TICKS);coverage+=ACTION_TICKS;
  if(!sameState(observeRace(check),frame.observation?.state))throw Error('Ghost state verification failed.');
  intervals.push(controls);
 }
 if(coverage<=180||observeRace(check).progress<=0)throw Error('Recording has no meaningful racing movement after countdown.');
 let state,cursor;
 const ghost={coverage,reset(){state=createRace(seed);cursor=0;},advanceTo(tick){
  if(!Number.isInteger(tick)||tick<cursor)throw Error('Restart the ghost before rewinding.');
  while(cursor<Math.min(tick,coverage)){stepRace(state,intervals[Math.floor(cursor/ACTION_TICKS)],1);cursor++;}
 },get observation(){return observeRace(state);},get ended(){return cursor>=coverage;}};
 ghost.reset();return ghost;
}

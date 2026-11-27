/** Strict playback of an independent benchmark recording on a shared tick clock.
 * Extra benchmark telemetry is allowed; every original physics observation is checked.
 */
import {createRace,stepRace,observeRace,isTerminal,TICK_RATE} from './racing.mjs';
export const COMPARISON_COLORS = Object.freeze(['#67c5ff','#ffce45','#ec91e9','#79dfb0','#ff9769','#bcb0ff','#dce990','#eff5fa']);
function containsState(actual,recorded) {
 if(typeof actual==='number')return typeof recorded==='number'&&Number.isFinite(recorded)&&Math.abs(actual-recorded)<=1e-9;
 if(actual&&typeof actual==='object')return recorded&&typeof recorded==='object'&&Object.keys(actual).every(key=>Object.hasOwn(recorded,key)&&containsState(actual[key],recorded[key]));
 return actual===recorded;
}
export function createComparisonGhost(replay,condition) {
 const config=replay?.config,cadence=condition.ticks_per_action;
 if(!['tidal-ring','tidal-ring-data-v1'].includes(condition.environment_id)||!condition.engine_sha256||replay?.environment_provenance?.engine_sha256!==condition.engine_sha256||
    replay.environment_id!==condition.environment_id||replay.environment_version!==condition.environment_version||
    config?.seed!==condition.seed||config?.tick_rate!==TICK_RATE||config.tick_rate!==condition.tick_rate||
    config.ticks_per_action!==cadence||config.max_ticks!==condition.max_ticks||!Number.isInteger(cadence)||cadence<1||cadence>240)
  throw Error('Recording does not match this comparison’s engine, seed or control rules.');
 if(!Array.isArray(replay.frames)||replay.frames.length>condition.max_ticks+1)throw Error('Invalid comparison recording.');
 const intervals=[];let verified=createRace(config.seed),coverage=0;
 for(const frame of replay.frames){
  if(frame.action===null&&frame.ticks_advanced===0)continue;
  const controls=frame.action?.controls,ticks=frame.ticks_advanced;
  if(!controls||Object.keys(controls).sort().join(',')!=='brake,reset,steer,throttle'||
     !Number.isInteger(ticks)||ticks<1||ticks>cadence||coverage+ticks>config.max_ticks)
   throw Error('Invalid recorded action interval.');
  for(const [key,value] of Object.entries(controls))if(typeof value!=='number'||!Number.isFinite(value)||value<(key==='steer'?-1:0)||value>1)throw Error('Invalid recorded controls.');
  if(condition.environment_id==='tidal-ring')stepRace(verified,controls,ticks);
  else for(let i=0;i<ticks&&!isTerminal(verified);i++)stepRace(verified,controls,1);
  coverage+=ticks;
  if(!containsState(observeRace(verified),frame.observation?.state))throw Error('Recorded physics state failed verification.');
  intervals.push({controls:structuredClone(controls),end:coverage});
 }
 let state,cursor,index;
 const ghost={coverage,reset(){state=createRace(config.seed);cursor=0;index=0;},advanceTo(tick){
  if(!Number.isSafeInteger(tick)||tick<cursor)throw Error('Restart before rewinding the comparison.');
  while(cursor<Math.min(tick,coverage)){
   while(intervals[index].end<=cursor)index++;
   if(!isTerminal(state))stepRace(state,intervals[index].controls,1);cursor++;
  }
 },get observation(){return observeRace(state);},get ended(){return cursor>=coverage;}};
 ghost.reset();return ghost;
}

/** Choose once by identity: manual targets survive polling/reordering. */
export function chooseComparisonTarget(records,manualId='') {
 const available=records.filter(record=>typeof record.episode_id==='string'&&record.episode_id);
 if(available.some(record=>record.episode_id===manualId))return manualId;
 const metrics=record=>record.metrics??record.result?.metrics??record.result_json?.metrics??{};
 const finished=record=>metrics(record).finished===1&&Number.isFinite(metrics(record).finish_time_seconds);
 available.sort((a,b)=>{
  const af=finished(a),bf=finished(b);
  if(af!==bf)return af?-1:1;
  const delta=af?metrics(a).finish_time_seconds-metrics(b).finish_time_seconds:
   (Number.isFinite(metrics(b).progress)?metrics(b).progress:0)-(Number.isFinite(metrics(a).progress)?metrics(a).progress:0);
  return delta||a.episode_id.localeCompare(b.episode_id);
 });
 return available[0]?.episode_id??'';
}

/** A follow target must actually be rendered in the chosen view. */
export function canFollowComparison(record,playableIds=[],mode='live') {
 if(mode==='replay')return typeof record?.episode_id==='string'&&playableIds.includes(record.episode_id);
 const state=record?.live_state?.state??record?.live_state;
 return !!state&&['x','z','heading'].every(key=>typeof state[key]==='number'&&Number.isFinite(state[key]));
}

/** Additive tick telemetry around the unchanged Tidal Ring physics.
 * The benchmark uses its own identity so existing recordings stay untouched.
 * Speed samples are tick-end simulation speeds, never decision averages.
 */
import * as racing from './racing.mjs';
export const {TRACK,TICK_RATE,MAX_TICKS,isTerminal,baselineControls}=racing;
export const ACTION_TICKS=30;
export const BENCHMARK=Object.freeze({id:'tidal-ring-data-v1',version:'1'});
const COUNTDOWN_TICKS=3*TICK_RATE;
export function createRace(seed=0){
  return {...racing.createRace(seed),telemetry:{ticks:0,speedSum:0,maxSpeed:0,lateralSum:0}};
}
export function stepRace(state,controls={},ticks=1){
  // Delegate control validation even for empty/terminal calls.
  racing.stepRace(state,controls,0);
  if(!Number.isSafeInteger(ticks)||ticks<0||ticks>MAX_TICKS)throw new Error('ticks must be a bounded non-negative integer');
  const events=[];
  for(let i=0;i<ticks&&!isTerminal(state);i++){
    const x=state.x,z=state.z;
    events.push(...racing.stepRace(state,controls,1));
    if(state.tick<=COUNTDOWN_TICKS)continue;
    // Finishing sets core speed to zero. The final on-road displacement retains
    // that tick's physical speed; other ticks sample the core's tick-end speed.
    const speed=state.phase==='finished'?Math.hypot(state.x-x,state.z-z)*TICK_RATE:state.speed;
    const telemetry=state.telemetry;
    telemetry.ticks++;
    telemetry.speedSum+=speed;
    telemetry.maxSpeed=Math.max(telemetry.maxSpeed,speed);
    telemetry.lateralSum+=Math.abs(racing.observeRace(state).lateral_offset);
  }
  return events;
}
export function raceMetrics(state){
  const metrics=racing.raceMetrics(state),telemetry=state.telemetry;
  return {...metrics,
    speed_sample_ticks:telemetry.ticks,
    max_speed_mps:telemetry.maxSpeed,
    mean_speed_mps:telemetry.ticks?telemetry.speedSum/telemetry.ticks:0,
    off_track_share:telemetry.ticks?state.offTrackTicks/telemetry.ticks:0,
    mean_absolute_lateral_m:telemetry.ticks?telemetry.lateralSum/telemetry.ticks:0};
}
export function observeRace(state){
  return {...racing.observeRace(state),metrics:raceMetrics(state)};
}

/** Docker executable: health or deterministic headless calibration with metrics. */
import { createRace,stepRace,isTerminal,baselineControls,raceMetrics } from '../frontend/src/gameplay/racing.mjs';
if(process.argv[2]==='stdio')await import('../scripts/racing_bridge.mjs');
else if(process.argv[2]==='health')console.log(await(await fetch(process.env.GAME_RUNTIME_URL||'http://127.0.0.1:8080/health')).text());
else if(process.argv[2]==='calibrate'){
 const state=createRace(Number(process.argv[3]||7));
 while(!isTerminal(state))stepRace(state,baselineControls(state),12);
 console.log(JSON.stringify({environment:'tidal-ring',seed:state.seed,tick:state.tick,metrics:raceMetrics(state)}));
 if(raceMetrics(state).finished!==1)process.exitCode=1;
}else{console.error('Usage: node game-runtime/cli.mjs health | calibrate [seed] | stdio');process.exitCode=2;}

/** Fixed-game executable: health, calibration or NDJSON control. */
import {games} from './games.mjs';
const game=games.get(process.argv[4]||'tidal-ring');
if(process.argv[2]==='stdio')await import(process.argv[3]==='tidal-ring-data-v1'?'../scripts/racing_benchmark_bridge.mjs':'../scripts/racing_bridge.mjs');
else if(process.argv[2]==='health')console.log(await(await fetch(process.env.GAME_RUNTIME_URL||'http://127.0.0.1:8080/health')).text());
else if(process.argv[2]==='calibrate'&&game){
 const state=game.create(Number(process.argv[3]||7));
 while(!game.terminal(state))game.step(state,game.baseline(state),game.id==='tidal-ring'?12:30);
 console.log(JSON.stringify({environment:game.id,seed:state.seed,tick:state.tick,metrics:game.metrics(state)}));
 if(game.id==='tidal-ring'&&game.metrics(state).finished!==1)process.exitCode=1;
}else{console.error('Usage: node game-runtime/cli.mjs health | calibrate [seed] [environment] | stdio [environment]');process.exitCode=2;}

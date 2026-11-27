/** Trusted fixed builds only; model actions never select executable source. */
import {createHash} from 'node:crypto';
import {readFileSync} from 'node:fs';
import {createRace,stepRace,observeRace,raceMetrics,isTerminal,baselineControls,TRACK} from '../frontend/src/gameplay/racing.mjs';
import * as benchmark from '../frontend/src/gameplay/racingBenchmark.mjs';
function engine(meta,module,create,step,observe,metrics,terminal,baseline){
 return {id:meta.id,version:meta.version,hash:createHash('sha256').update(Buffer.concat((Array.isArray(module)?module:[module]).map(path=>readFileSync(new URL(path,import.meta.url))))).digest('hex'),create,step,observe,metrics,terminal,baseline};
}
export const games=new Map([
 ['tidal-ring',engine(TRACK,'../frontend/src/gameplay/racing.mjs',createRace,stepRace,observeRace,raceMetrics,isTerminal,baselineControls)],
 ['tidal-ring-data-v1',engine(benchmark.BENCHMARK,['../frontend/src/gameplay/racingBenchmark.mjs','../frontend/src/gameplay/racing.mjs'],benchmark.createRace,benchmark.stepRace,benchmark.observeRace,benchmark.raceMetrics,benchmark.isTerminal,benchmark.baselineControls)],
]);
export const metadata=game=>({environment_id:game.id,environment_version:game.version,engine_sha256:game.hash,node_version:process.version});

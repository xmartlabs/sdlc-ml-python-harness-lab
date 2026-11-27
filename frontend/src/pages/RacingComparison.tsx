import {useEffect,useRef,useState} from 'react';
import type {CSSProperties} from 'react';
import {Link} from 'react-router-dom';
import {SESSION_EXPIRED_EVENT} from '../api';
import {createComparisonGhost,chooseComparisonTarget,canFollowComparison,COMPARISON_COLORS} from '../gameplay/comparisonGhost.mjs';
import type {ComparisonCondition,ComparisonGhost} from '../gameplay/comparisonGhost.mjs';
import type {RaceObservation} from '../gameplay/racing.mjs';
import {createComparisonScene} from '../gameplay/comparisonScene';
import './RacingComparison.css';

type Numbers=Record<string,unknown>;
type Competitor={label:string;model:string;episode_id?:string;state?:string;status?:string;metrics?:Numbers;live_state?:RaceObservation;result?:Numbers;result_json?:Numbers;failure_reason?:string;[key:string]:unknown};
type Comparison={condition:ComparisonCondition;competitors:Competitor[];session_id?:string;[key:string]:unknown};
type Recording=Competitor&Partial<ComparisonCondition>&{controller:string};
type Source={id:string;ghost?:ComparisonGhost;error?:string};
const ROOT='/api/gameplay/racing/comparisons';
async function request<T>(url:string,signal?:AbortSignal,options?:RequestInit):Promise<T>{
 const response=await fetch(url,{credentials:'same-origin',signal,...options,headers:{'Content-Type':'application/json',...options?.headers}});
 if(response.status===401)window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));
 if(!response.ok){const data=await response.json().catch(()=>({}));throw Error(typeof data.detail==='string'?data.detail:`Request failed (${response.status}).`);}
 return response.json();
}
const finite=(value:unknown)=>typeof value==='number'&&Number.isFinite(value)?value:undefined;
function facts(c:Competitor):Numbers{return {...c.result_json,...c.result,...c.metrics,...c};}
function metric(c:Competitor,...names:string[]){const f=facts(c);for(const key of names){const n=finite(f[key]);if(n!==undefined)return n;}return undefined;}
const number=(value:number|undefined,digits=2)=>value===undefined?'—':value.toFixed(digits);
function finalStatus(c:Competitor){
 if(typeof c.outcome_label==='string')return c.outcome_label;
 const s=c.status??String(c.result?.status??c.result_json?.status??c.state??'waiting');
 if(metric(c,'finished')===1)return 'FINISHED';
 if(['waiting','pending','queued','running','loading'].includes(s))return s;
 if(['completed','budget_exhausted','dnf','simulation_limit','sim_limit'].includes(s))return 'DNF · simulation limit';
 return s.replaceAll('_',' ');
}
function getLive(c:Competitor):RaceObservation|undefined{
 const state=c.live_state as unknown as {state?:RaceObservation}|undefined;
 const candidate=state?.state??c.live_state;
 return candidate&&Number.isFinite(candidate.x)&&Number.isFinite(candidate.z)&&Number.isFinite(candidate.heading)?candidate:undefined;
}

export function RacingComparison(){
 const [data,setData]=useState<Comparison|null>(null),[error,setError]=useState(''),[renderError,setRenderError]=useState('');
 const [sources,setSources]=useState<Record<string,Source>>({}),[loading,setLoading]=useState(false);
 const [reload,setReload]=useState(0);
 const [recordings,setRecordings]=useState<Recording[]>([]),[selected,setSelected]=useState<string[]>([]),[studioError,setStudioError]=useState(''),[studioBusy,setStudioBusy]=useState(false);
 const mutation=useRef(false),generation=useRef(0);
 const [cameraMode,setCameraMode]=useState<'overview'|'follow'>('overview'),[target,setTarget]=useState({id:'',manual:false});
 const cameraRef=useRef<{mode:'overview'|'follow';target:number}>({mode:'overview',target:0});
 const recordsKey=(r:Recording)=>JSON.stringify([r.environment_id,r.environment_version,r.seed,r.tick_rate,r.ticks_per_action,r.max_ticks,r.engine_sha256]);
 const selectedRecords=recordings.filter(r=>selected.includes(r.episode_id??''));
 const selectedCondition=selectedRecords[0]?recordsKey(selectedRecords[0]):'';
 const refreshRecordings=()=>request<Recording[]>(`${ROOT}/recordings`).then(items=>{if(!Array.isArray(items))throw Error('Invalid recordings list.');setRecordings(items);}).catch(e=>setStudioError((e as Error).message));
 const createSession=async()=>{mutation.current=true;generation.current++;playingRef.current=false;setPlaying(false);setStudioBusy(true);setStudioError('');try{
  const result=await request<Comparison>(`${ROOT}/sessions`,undefined,{method:'POST',body:JSON.stringify({episode_ids:selected})});dataRef.current=result;setData(result);setMode('replay');modeRef.current='replay';
 }catch(e){setStudioError((e as Error).message);}finally{generation.current++;mutation.current=false;setStudioBusy(false);}};
 const createExamples=async()=>{mutation.current=true;generation.current++;playingRef.current=false;setPlaying(false);setStudioBusy(true);setStudioError('');try{
  const result=await request<Comparison>(`${ROOT}/examples`,undefined,{method:'POST',body:'{}'});dataRef.current=result;setData(result);setSelected(result.competitors.flatMap(c=>c.episode_id?[c.episode_id]:[]));setMode('replay');modeRef.current='replay';await refreshRecordings();
 }catch(e){setStudioError((e as Error).message);}finally{generation.current++;mutation.current=false;setStudioBusy(false);}};
 const returnToBenchmark=async()=>{mutation.current=true;generation.current++;playingRef.current=false;setPlaying(false);setStudioBusy(true);setStudioError('');try{
  const result=await request<Comparison>(`${ROOT}/sessions/current`,undefined,{method:'DELETE'});dataRef.current=result;setData(result);setSelected([]);setMode('live');modeRef.current='live';
 }catch(e){setStudioError((e as Error).message);}finally{generation.current++;mutation.current=false;setStudioBusy(false);}};
 useEffect(()=>{void refreshRecordings();},[]);
 const loadedCondition=useRef('');
 const [mode,setMode]=useState<'live'|'replay'>('live'),[playing,setPlaying]=useState(false),[tick,setTick]=useState(0);
 const playableIds=Object.keys(sources).filter(id=>sources[id].ghost);
 const followable=(c:Competitor)=>canFollowComparison(c,playableIds,mode);
 const canvas=useRef<HTMLCanvasElement>(null),dataRef=useRef(data),sourceRef=useRef(sources),modeRef=useRef(mode),playingRef=useRef(playing),clock=useRef(0);
 const conditionKey=data?`${data.session_id??data.id??''}:${JSON.stringify(data.condition)}`:'';
 const recordingKey=data?.competitors.map(c=>`${c.episode_id??''}:${c.state??''}:${c.status??''}`).join('|')??'';
 useEffect(()=>{dataRef.current=data;},[data]);useEffect(()=>{sourceRef.current=sources;},[sources]);
 useEffect(()=>{modeRef.current=mode;},[mode]);useEffect(()=>{playingRef.current=playing;},[playing]);
 useEffect(()=>{
  let disposed=false,controller:AbortController|undefined;
  const refresh=async()=>{const startedGeneration=generation.current;controller=new AbortController();try{
   const result=await request<Comparison>(`${ROOT}/current`,controller.signal);
   if(!result.condition||!Array.isArray(result.competitors)||result.competitors.length>8)throw Error('Invalid comparison session.');
   if(!disposed&&!mutation.current&&startedGeneration===generation.current){dataRef.current=result;setData(result);setError('');}
  }catch(e){if(!disposed&&(e as Error).name!=='AbortError')setError((e as Error).message);}};
  void refresh();const timer=window.setInterval(()=>void refresh(),2000);
  return()=>{disposed=true;controller?.abort();clearInterval(timer);};
 },[]);
 useEffect(()=>{
  if(!data)return;
  const controller=new AbortController();let disposed=false;setLoading(true);
  if(loadedCondition.current!==conditionKey){loadedCondition.current=conditionKey;sourceRef.current={};setSources({});clock.current=0;setTick(0);setPlaying(false);playingRef.current=false;}
  const load=async()=>{
   const entries=await Promise.all(data.competitors.filter(c=>c.episode_id&&!['waiting','running','pending','queued','loading','blocked'].includes(c.state??c.status??'')).map(async c=>{
    const id=c.episode_id!,existing=sourceRef.current[id];
    if(existing?.ghost)return [id,existing] as const;
    try{const replay=await request<unknown>(`${ROOT}/episodes/${encodeURIComponent(id)}/replay`,controller.signal);return [id,{id,ghost:createComparisonGhost(replay,data.condition)}] as const;}
    catch(e){if((e as Error).name==='AbortError')throw e;return [id,{id,error:(e as Error).message}] as const;}
   }));
   if(!disposed){const nextSources=Object.fromEntries(entries) as Record<string,Source>;sourceRef.current=nextSources;setSources(nextSources);setLoading(false);}
  };
  void load().catch(e=>{if(!disposed){setError((e as Error).message);setLoading(false);}});
  return()=>{disposed=true;controller.abort();};
 // Only completed identities/condition changes rebuild deterministic playback.
 // eslint-disable-next-line react-hooks/exhaustive-deps
 },[conditionKey,recordingKey,reload]);
 useEffect(()=>{
  if(!canvas.current)return;let scene:ReturnType<typeof createComparisonScene>;
  try{scene=createComparisonScene(canvas.current);}catch{setRenderError('WebGL is unavailable. Numerical results and original replay files remain accessible below.');return;}
  let animation=0,previous=0,accumulator=0,lastTick=-1;
  const frame=(now:number)=>{
   const current=dataRef.current;
   if(current&&modeRef.current==='replay'&&playingRef.current){
    accumulator+=previous?Math.min((now-previous)/1000,.1):0;
    const rate=current.condition.tick_rate;
    const end=Math.max(0,...Object.values(sourceRef.current).map(s=>s.ghost?.coverage??0));
    while(accumulator>=1/rate&&clock.current<end){clock.current++;accumulator-=1/rate;}
    if(clock.current>=end){playingRef.current=false;setPlaying(false);}
   }else accumulator=0;
   previous=now;
   const views=current?.competitors.map(c=>{
    if(modeRef.current==='live')return getLive(c);
    const ghost=c.episode_id?sourceRef.current[c.episode_id]?.ghost:undefined;
    ghost?.advanceTo(clock.current);return ghost?.observation;
   })??[];
   scene.render(views,cameraRef.current);
   if(clock.current!==lastTick&&(clock.current%6===0||!playingRef.current)){setTick(clock.current);lastTick=clock.current;}
   animation=requestAnimationFrame(frame);
  };
  animation=requestAnimationFrame(frame);
  const pause=()=>{playingRef.current=false;setPlaying(false);};
  window.addEventListener('blur',pause);document.addEventListener('visibilitychange',pause);
  return()=>{cancelAnimationFrame(animation);scene.dispose();window.removeEventListener('blur',pause);document.removeEventListener('visibilitychange',pause);};
 },[Boolean(data)]);
 useEffect(()=>{
  if(!data)return;
  setTarget(previous=>{
   if(previous.manual&&data.competitors.some(c=>c.episode_id===previous.id&&followable(c)))return previous;
   const candidates=data.competitors.filter(followable);
   const id=chooseComparisonTarget(candidates);return id===previous.id?previous:{id,manual:false};
  });
 },[data,sources,mode]);
 useEffect(()=>{if(!target.id)setCameraMode('overview');},[target.id]);
 useEffect(()=>{cameraRef.current={mode:target.id?cameraMode:'overview',target:Math.max(0,data?.competitors.findIndex(c=>c.episode_id===target.id)??0)};},[cameraMode,target.id,data]);
 useEffect(()=>{
  const keyboard=(event:KeyboardEvent)=>{
   if(event.repeat||event.altKey||event.ctrlKey||event.metaKey||!(event.target instanceof Element)||event.target.closest('input,textarea,select')||(event.target instanceof HTMLElement&&event.target.isContentEditable))return;
   const n=Number(event.key);if(!Number.isInteger(n)||n<1||n>8)return;
   const candidate=dataRef.current?.competitors[n-1];
   if(!candidate||!canFollowComparison(candidate,Object.keys(sourceRef.current).filter(id=>sourceRef.current[id].ghost),modeRef.current))return;
   const id=candidate.episode_id;if(!id)return;
   event.preventDefault();setTarget({id,manual:true});setCameraMode('follow');
  };
  window.addEventListener('keydown',keyboard);return()=>window.removeEventListener('keydown',keyboard);
 },[]);
 const seek=(next:number)=>{Object.values(sourceRef.current).forEach(s=>{s.ghost?.reset();s.ghost?.advanceTo(next);});clock.current=next;setTick(next);setPlaying(false);playingRef.current=false;setMode('replay');modeRef.current='replay';};
 const restart=()=>{Object.values(sourceRef.current).forEach(s=>s.ghost?.reset());clock.current=0;setTick(0);setPlaying(false);playingRef.current=false;setMode('replay');modeRef.current='replay';};
 const play=()=>{const end=Math.max(0,...Object.values(sourceRef.current).map(s=>s.ghost?.coverage??0));if(mode!=='replay'||clock.current>=end)restart();setMode('replay');modeRef.current='replay';playingRef.current=true;setPlaying(true);};
 const available=data?.competitors.filter(c=>c.episode_id&&sources[c.episode_id]?.ghost).length??0;
 const rate=data?.condition.tick_rate??60;
 return <div className="comparison-page">
  <header className="comparison-heading"><div><p className="comparison-kicker">RACING / DATA-ONLY BENCHMARK</p><h1>Compare recorded drivers.</h1><p>Select one to eight compatible human or AI recordings. Ghosts are independent replays, never live opponents.</p></div><Link to="/gameplay/racing">Drive the course yourself →</Link></header>
  <section className="comparison-picker" aria-labelledby="comparison-picker-title">
   <h2 id="comparison-picker-title">Build a comparison</h2><p>Use your saved drives, actual model attempts, or clearly labeled scripted examples. Choose recordings with matching engine, seed, action cadence and time limit.</p>
   <div className="comparison-controls"><button disabled={studioBusy} onClick={()=>void refreshRecordings()}>Refresh saved recordings</button><button disabled={studioBusy} onClick={()=>void createExamples()}>{studioBusy?'Working…':'Create two SCRIPTED examples'}</button><Link to="/gameplay/racing">Record your own drive →</Link></div>
   {studioError&&<p className="comparison-error" role="alert">{studioError}</p>}
   {recordings.length===0?<p className="comparison-empty">No registered recordings yet. Record a drive or create the scripted examples to get started.</p>:<div className="comparison-recordings" aria-label="Select saved recordings">{recordings.map(r=>{
    const id=r.episode_id??'',checked=selected.includes(id),compatible=!selectedCondition||recordsKey(r)===selectedCondition;
    return <label key={id} className="comparison-recording"><input type="checkbox" checked={checked} disabled={studioBusy||(!checked&&(!compatible||selected.length>=8))} onChange={()=>setSelected(old=>checked?old.filter(value=>value!==id):[...old,id])}/><span><strong>{r.label||r.model||'Human driver'}</strong><small>{r.controller==='calibration'?'SCRIPTED calibration':r.controller==='human'?'Human recording':r.model||r.controller} · {r.status??r.state??'recorded'}</small><small>{r.environment_id} · seed {r.seed} · {r.ticks_per_action!=null&&r.tick_rate?`${(r.ticks_per_action/r.tick_rate).toFixed(2)}s/action`:'cadence unavailable'}{!compatible?' · different comparison conditions':''}</small><small>{id}</small></span></label>;
   })}</div>}
   <div className="comparison-controls"><button className="comparison-create" disabled={studioBusy||selected.length===0||selected.length>8} onClick={()=>void createSession()}>{studioBusy?'Creating…':`Compare ${selected.length} selected recording${selected.length===1?'':'s'}`}</button><button disabled={!selected.length||studioBusy} onClick={()=>setSelected([])}>Clear selection</button><span>{selected.length} / 8 selected</span>{data?.session_id&&<button disabled={studioBusy} onClick={()=>void returnToBenchmark()}>Return to current benchmark</button>}</div>
  </section>
  {error&&<p className="comparison-error" role="alert">{error}</p>}
  {!data&&<p className="comparison-empty">Waiting for a benchmark session. The comparison dashboard will update automatically.</p>}
  {data&&<>
   <div className="comparison-condition"><span>Seed {data.condition.seed}</span><span>{data.condition.tick_rate} Hz simulation</span><span>{(data.condition.ticks_per_action/data.condition.tick_rate).toFixed(2)}s per action</span><span>{(data.condition.max_ticks/data.condition.tick_rate).toFixed(0)}s total simulation limit</span><span>Build {String(data.condition.engine_sha256??'not frozen').slice(0,12)}</span></div>
   <p className="comparison-note">{mode==='live'?(data.session_id?'SAVED FINAL STATES: these are selected recordings’ final persisted positions, not a live race. Play the recordings to compare them on one simulation clock.':'LIVE SNAPSHOTS: each model’s most recently persisted state, not a synchronized race. Simulation freezes during inference; wall time and inference latency are measured separately.'):'RECORDED COMPARISON: every car advances from its own original controls on this shared simulation clock. A partial attempt freezes at its final recorded state. Numbered colors identify drivers even where cars overlap.'}</p>
   <div className="comparison-controls" aria-label="Replay and camera controls">
    <button aria-pressed={cameraMode==='overview'} onClick={()=>setCameraMode('overview')}>Overview camera</button>
    <button aria-pressed={cameraMode==='follow'} disabled={!data.competitors.some(followable)} onClick={()=>setCameraMode('follow')}>Follow camera</button>
    <label htmlFor="comparison-camera-target">Follow driver</label><select id="comparison-camera-target" value={target.id} onChange={event=>{setTarget({id:event.target.value,manual:true});setCameraMode('follow');}}><option value="" disabled>Select an available recording</option>{data.competitors.map((c,i)=><option disabled={!followable(c)} key={c.episode_id??i} value={c.episode_id??''}>{i+1}. {c.label||c.model}{!followable(c)?' · no rendered state':''}</option>)}</select>
    <button disabled={!data.competitors.some(followable)} onClick={()=>setTarget({id:chooseComparisonTarget(data.competitors.filter(followable)),manual:false})}>Follow best recording</button>
    <button disabled={mode==='live'} onClick={()=>{setMode('live');setPlaying(false);playingRef.current=false;}}>{data.session_id?'Saved final states':'Live progress'}</button>
    <button disabled={!available||loading||playing||studioBusy} onClick={play}>{playing?'Playing…':`Play ${available} available ghost${available===1?'':'s'}`}</button>
    <button disabled={!playing} onClick={()=>{setPlaying(false);playingRef.current=false;}}>Pause</button>
    <button disabled={!available||loading||studioBusy} onClick={restart}>Restart comparison</button>
    <button disabled={loading||studioBusy} onClick={()=>setReload(n=>n+1)}>Reload recordings</button>
    <span className="comparison-clock" aria-live="off">{mode==='replay'?`${(tick/rate).toFixed(2)}s simulation`:data.session_id?'Saved final states':'Latest live states'}</span>
   </div>
   {mode==='replay'&&available>0&&<label className="comparison-seek" htmlFor="comparison-seek">Replay time (simulation seconds)<input id="comparison-seek" type="range" min={0} max={Math.max(0,...Object.values(sources).map(s=>s.ghost?.coverage??0))} step={data.condition.tick_rate} value={tick} onChange={event=>seek(Number(event.target.value))}/></label>}
   <p className="comparison-shortcuts">Camera: overview by default. Follow the best valid lap, or select a driver with 1–8 or the selector. Number keys leave typing fields alone. Camera changes never alter physics.</p>
   {renderError&&<p className="comparison-error" role="alert">{renderError}</p>}
   <div className="comparison-inline-legend" aria-label="Ghost color key">{data.competitors.map((c,i)=><span key={c.episode_id??i} style={{'--driver-color':COMPARISON_COLORS[i]} as CSSProperties}><b>{i+1}</b><button disabled={!followable(c)} aria-label={`Follow driver ${i+1}: ${c.label||c.model}`} aria-pressed={target.id===c.episode_id&&cameraMode==='follow'} onClick={()=>{if(c.episode_id&&followable(c)){setTarget({id:c.episode_id,manual:true});setCameraMode('follow');}}}>{c.label||c.model}</button></span>)}</div>
   <canvas ref={canvas} className="comparison-canvas" aria-label="All recorded racing ghosts, independently simulated and labeled by driver number"/>
   <div className="comparison-legend" aria-label="Driver colors and recording coverage">{data.competitors.map((c,i)=>{
    const source=c.episode_id?sources[c.episode_id]:undefined,ghost=source?.ghost,view=mode==='replay'?ghost?.observation:getLive(c);
    return <article key={`${i}-${c.model}`} className="comparison-competitor" style={{'--driver-color':COMPARISON_COLORS[i]} as CSSProperties}>
     <h3><span className="comparison-badge">{i+1}</span>{c.label||c.model}</h3><small>{c.model}</small>
     <p className={c.state==='running'?'comparison-live':''}>{finalStatus(c)}</p>
     <p>{view?`${view.game_time_seconds.toFixed(2)}s racing · ${(view.progress*100).toFixed(1)}% · ${view.checkpoints_passed}/12 gates`:'No persisted driving state yet.'}</p>
     {mode==='replay'&&ghost&&<p>{ghost.ended?'Recording ended · frozen':'Recorded playback'} · {(ghost.coverage/rate).toFixed(2)}s coverage including countdown</p>}
     {source?.error&&<p className="comparison-error">Replay unavailable: {source.error}</p>}
     {c.failure_reason&&<p>{c.failure_reason}</p>}
     {c.episode_id&&<small>{c.episode_id}</small>}
    </article>;
   })}</div>
   <h2>Driving outcomes</h2><p>Finishers are compared by valid lap time. Unfinished driving progress is a DNF snapshot; provider errors and interruptions remain explicit.</p>
   <div className="comparison-tables"><table className="comparison-table"><thead><tr><th>Driver</th><th>Outcome / finish</th><th>Progress / gates</th><th>Speed max / tick mean</th><th>Off-track time / share</th><th>Collisions / resets</th><th>Mean lateral deviation</th><th>Recording</th></tr></thead><tbody>{data.competitors.map((c,i)=><tr key={`${i}-${c.model}`}><td>{i+1}. {c.label||c.model}</td><td>{finalStatus(c)}<small>{metric(c,'finished')===1?`${number(metric(c,'finish_time_seconds'))}s valid lap`:`${number(metric(c,'race_time_seconds'))}s simulated driving`}</small></td><td>{number((metric(c,'progress')??getLive(c)?.progress)===undefined?undefined:(metric(c,'progress')??getLive(c)!.progress)*100,1)}%<small>{number(metric(c,'checkpoints_passed')??getLive(c)?.checkpoints_passed,0)} / 12 gates</small></td><td>{number(metric(c,'max_speed_mps','max_speed'))} / {number(metric(c,'mean_speed_mps','mean_speed'))} m/s</td><td>{number(metric(c,'off_track_seconds'))}s<small>{number(metric(c,'off_track_share')===undefined?undefined:metric(c,'off_track_share')!*100,1)}%</small></td><td>{number(metric(c,'collisions'),0)} / {number(metric(c,'resets'),0)}</td><td>{number(metric(c,'mean_absolute_lateral_m'))}m</td><td>{c.episode_id&&sources[c.episode_id]?.ghost?<a href={`${ROOT}/episodes/${encodeURIComponent(c.episode_id)}/replay`} target="_blank" rel="noreferrer">Original replay JSON ↗</a>:c.state==='running'?'Recording in progress':'Pending / unavailable'}</td></tr>)}</tbody></table></div>
   <h2>Inference and validity</h2><p>Game time excludes waiting for providers. Missing values are unavailable, not zero. No hidden source inspection or live model control is used to draw ghosts.</p>
   <div className="comparison-tables"><table className="comparison-table"><thead><tr><th>Driver</th><th>Valid / invalid actions</th><th>Decision median / p95</th><th>Inference / wall time</th><th>Requests / tokens in / out</th><th>Provenance / live state</th></tr></thead><tbody>{data.competitors.map((c,i)=><tr key={`${i}-${c.model}`}><td>{i+1}. {c.label||c.model}</td><td>{number(metric(c,'valid_actions'),0)} / {number(metric(c,'invalid_actions'),0)}</td><td>{number(metric(c,'latency_p50_seconds','decision_latency_p50_seconds'))}s / {number(metric(c,'latency_p95_seconds','decision_latency_p95_seconds'))}s</td><td>{number(metric(c,'inference_seconds'))}s / {number(metric(c,'wall_seconds','wall_time_seconds'))}s</td><td>{number(metric(c,'request_count','requests'),0)} / {number(metric(c,'input_tokens','prompt_tokens'),0)} / {number(metric(c,'output_tokens','completion_tokens'),0)}</td><td><details><summary>Inspect recorded facts</summary><pre style={{whiteSpace:'pre-wrap',maxWidth:430,fontSize:'.7rem'}}>{JSON.stringify(c,null,2)}</pre></details></td></tr>)}</tbody></table></div>
  </>}
 </div>;
}

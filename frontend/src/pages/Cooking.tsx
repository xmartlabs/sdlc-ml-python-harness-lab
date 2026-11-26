import { useEffect, useRef, useState } from 'react';
import { COOKING, COOK_TICK_RATE, COOK_ACTION_TICKS, createKitchen, stepKitchen, observeKitchen, kitchenTerminal } from '../gameplay/cooking.mjs';
import type { KitchenControls, KitchenMetrics, KitchenObservation } from '../gameplay/cooking.mjs';
import { createCookingGhost } from '../gameplay/cookingGhost.mjs';
import type { CookingGhostTimeline } from '../gameplay/cookingGhost.mjs';
import { SESSION_EXPIRED_EVENT } from '../api';
import './Cooking.css';

type Model = {id:string;bare_id:string;loaded:boolean};
type Episode = {episode_id:string;controller:string;model:string;state:string;failure_reason:string;result_json:{metrics?:KitchenMetrics;status?:string;inference_seconds?:number}};
type GhostSource = {episode_id:string;label:string;partial:boolean;status:string;recording:unknown};
type Recording = {config:{seed:number};frames:{action:{controls:KitchenControls}|null}[]};
async function request<T>(path:string,options?:RequestInit):Promise<T> {
 const response=await fetch(`/api/gameplay/cooking${path}`,{credentials:'same-origin',...options,headers:{'Content-Type':'application/json',...options?.headers}});
 if(response.status===401)window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));
 if(!response.ok){const detail=await response.json().catch(()=>({}));throw Error(typeof detail.detail==='string'?detail.detail:'Request failed. Check your session and the API.');}
 return response.json();
}
async function buildHash():Promise<string>{
 const response=await fetch('/game/cooking/build.json');
 if(!response.ok)throw Error('Cooking build metadata is unavailable.');
 return (await response.json()).engine_sha256;
}
const SEED=7;
const IDLE:KitchenControls={target:-1,interact:0};

export function Cooking(){
 const area=useRef<HTMLElement>(null),state=useRef(createKitchen(SEED)),actions=useRef<KitchenControls[]>([]);
 const playing=useRef(false),paused=useRef(false),generation=useRef(0),target=useRef(-1),working=useRef(false);
 const heldKeys=useRef(new Set<string>()),playback=useRef<KitchenControls[]|null>(null),isPlayback=useRef(false);
 const ghost=useRef<CookingGhostTimeline|null>(null),reference=useRef<GhostSource|null>(null),challenge=useRef(false),attemptGhost=useRef<string|undefined>(undefined);
 const [view,setView]=useState(()=>observeKitchen(state.current)),[phase,setPhase]=useState<'ready'|'playing'|'paused'|'ended'>('ready');
 const [selected,setSelected]=useState(-1),[workButton,setWorkButton]=useState(false),[source,setSource]=useState<GhostSource|null>(null);
 const [episodes,setEpisodes]=useState<Episode[]>([]),[models,setModels]=useState<Model[]>([]),[model,setModel]=useState('');
 const [error,setError]=useState(''),[recordsError,setRecordsError]=useState(''),[busy,setBusy]=useState(false),[saving,setSaving]=useState(false),[launching,setLaunching]=useState(false),[saved,setSaved]=useState('');
 const refresh=()=>request<Episode[]>('/episodes').then(items=>{setEpisodes(items);setRecordsError('');}).catch(e=>setRecordsError(e.message));
 const refreshModels=()=>request<Model[]>('/models').then(items=>{setModels(items);setModel(previous=>items.some(m=>m.id===previous&&m.loaded)?previous:items.find(m=>m.loaded)?.id??'');}).catch(e=>setRecordsError(e.message));
 useEffect(()=>{void refresh();void refreshModels();const timer=window.setInterval(()=>void refresh(),3000);return()=>clearInterval(timer);},[]);
 useEffect(()=>{
  let animation=0,previous=0,accumulator=0,remaining=0,epoch=-1,controls=IDLE;
  const release=()=>{working.current=false;heldKeys.current.clear();setWorkButton(false);};
  const pause=()=>{release();if(playing.current){paused.current=true;setPhase('paused');}};
  const keyboard=(event:KeyboardEvent)=>{
   const key=event.key.toLowerCase();
   if(event.type==='keyup'){heldKeys.current.delete(key);return;}
   if(!area.current?.contains(document.activeElement)||!playing.current)return;
   if(['1','2','3','4','5','6',' ','e','escape'].includes(key))event.preventDefault();
   if(key==='escape'){pause();return;}
   if(/^[1-6]$/.test(key)){target.current=Number(key)-1;setSelected(target.current);}
   if(key===' '||key==='e')heldKeys.current.add(key);
  };
  const hidden=()=>{if(document.hidden)pause();};
  const pointerRelease=()=>{working.current=false;setWorkButton(false);};
  window.addEventListener('keydown',keyboard);window.addEventListener('keyup',keyboard);window.addEventListener('blur',pause);window.addEventListener('pointerup',pointerRelease);window.addEventListener('pointercancel',pointerRelease);document.addEventListener('visibilitychange',hidden);
  const frame=(now:number)=>{
   if(epoch!==generation.current){epoch=generation.current;remaining=0;accumulator=0;previous=now;controls=IDLE;}
   if(playing.current&&!paused.current){
    accumulator+=previous?Math.min((now-previous)/1000,.15):0;
    let changed=false;
    while(accumulator>=1/COOK_TICK_RATE&&!kitchenTerminal(state.current)){
     if(remaining===0){
      controls={target:target.current,interact:working.current||heldKeys.current.has(' ')||heldKeys.current.has('e')?1:0};
      if(playback.current){const next=playback.current[actions.current.length];if(!next){playing.current=false;setPhase('ended');release();break;}controls=next;}
      actions.current.push({...controls});remaining=COOK_ACTION_TICKS;
     }
     stepKitchen(state.current,controls,1);if(challenge.current)ghost.current?.advanceTo(state.current.tick);
     remaining--;accumulator-=1/COOK_TICK_RATE;changed=true;
    }
    if(changed)setView(observeKitchen(state.current));
    if(kitchenTerminal(state.current)){playing.current=false;setPhase('ended');release();}
   }else accumulator=0;
   previous=now;animation=requestAnimationFrame(frame);
  };
  animation=requestAnimationFrame(frame);
  return()=>{cancelAnimationFrame(animation);window.removeEventListener('keydown',keyboard);window.removeEventListener('keyup',keyboard);window.removeEventListener('blur',pause);window.removeEventListener('pointerup',pointerRelease);window.removeEventListener('pointercancel',pointerRelease);document.removeEventListener('visibilitychange',hidden);};
 },[]);
 const select=(id:number)=>{target.current=id;setSelected(id);area.current?.focus();};
 const start=()=>{
  state.current=createKitchen(SEED);actions.current=[];playback.current=null;isPlayback.current=false;generation.current++;
  ghost.current?.reset();challenge.current=!!ghost.current;attemptGhost.current=reference.current?.episode_id;
  target.current=-1;working.current=false;heldKeys.current.clear();setSelected(-1);setWorkButton(false);paused.current=false;playing.current=true;
  setSaved('');setError('');setView(observeKitchen(state.current));setPhase('playing');area.current?.focus();
 };
 const pause=()=>{paused.current=true;working.current=false;heldKeys.current.clear();setWorkButton(false);setPhase('paused');};
 const resume=()=>{paused.current=false;setPhase('playing');area.current?.focus();};
 const chooseGhost=async(id?:string)=>{
  setBusy(true);setError('');
  try{const episodeId=id??(await request<Episode>('/ghosts/calibration',{method:'POST'})).episode_id;
   const selectedSource=await request<GhostSource>(`/ghosts/${episodeId}?seed=${SEED}`);
   const timeline=createCookingGhost(selectedSource.recording,await buildHash(),SEED);
   reference.current=selectedSource;ghost.current=timeline;setSource(selectedSource);start();await refresh();
  }catch(e){setError((e as Error).message);}finally{setBusy(false);}
 };
 const watch=async(episode:Episode)=>{
  setBusy(true);setError('');
  try{const recording=await request<Recording>(`/episodes/${episode.episode_id}/replay`);
   createCookingGhost(recording,await buildHash(),SEED,{forChallenge:false});
   state.current=createKitchen(recording.config.seed);actions.current=[];playback.current=recording.frames.filter(f=>f.action).map(f=>f.action!.controls);isPlayback.current=true;
   generation.current++;challenge.current=false;attemptGhost.current=undefined;target.current=-1;working.current=false;heldKeys.current.clear();setSelected(-1);setWorkButton(false);
   paused.current=false;playing.current=true;setSaved(episode.episode_id);setView(observeKitchen(state.current));setPhase('playing');area.current?.focus();
  }catch(e){setError((e as Error).message);}finally{setBusy(false);}
 };
 const save=async()=>{setSaving(true);setError('');try{const row=await request<Episode>('/human',{method:'POST',body:JSON.stringify({seed:SEED,actions:actions.current,ghost_episode_id:attemptGhost.current})});if(row.state==='failed')throw Error(row.failure_reason);setSaved(row.episode_id);await refresh();}catch(e){setError((e as Error).message);}finally{setSaving(false);}};
 const launch=async()=>{setLaunching(true);setError('');try{const row=await request<Episode>('/episodes',{method:'POST',body:JSON.stringify({seed:SEED,model})});if(row.state==='failed')throw Error(row.failure_reason);await refresh();}catch(e){setError((e as Error).message);}finally{setLaunching(false);}};
 const locked=phase==='playing'||phase==='paused'||busy||saving;
 const ghostView=challenge.current?ghost.current?.observation:undefined;
 const progress=(k:KitchenObservation)=>`${k.held} held · pot ${k.pot.stage}`;
 return <main className="cooking-page">
  <header><p className="cooking-kicker">GAMEPLAY LAB · LOCAL DEMO</p><h1>Harbor Kitchen</h1><p>A fixed 90-second tomato soup shift. Original small kitchen · seed {SEED} · rules {COOKING.version}.</p></header>
  <section className="cooking-panel" aria-label="Recorded cooking ghost">
   <h2>{source?`Ghost: ${source.label}`:'Cook alongside a recorded model'}</h2>
   <p>{source?`${source.partial?'PARTIAL recording':'Complete recorded shift'} · ${source.status} · ${source.episode_id}`:'Ghosts replay their own kitchen on your simulation clock. They cannot use your ingredients, pot, or orders.'}</p>
   <div className="cooking-buttons"><button disabled={locked} onClick={()=>void chooseGhost()}>Practice vs SCRIPTED reference</button>{source&&<button disabled={locked} onClick={()=>{ghost.current=null;reference.current=null;challenge.current=false;setSource(null);}}>Clear ghost</button>}</div>
   {busy&&<p role="status">Loading and verifying recording…</p>}
  </section>
  {error&&<p className="cooking-error" role="alert">{error}</p>}
  <section ref={area} tabIndex={0} className="cooking-panel cooking-game" aria-label="Kitchen. Keys 1 to 6 select a station, hold E or Space to work, Escape pauses.">
   <div className="cooking-hud"><strong>{view.phase==='countdown'?`Starts in ${Math.ceil(view.countdown_seconds)}s`:`${Math.ceil(view.remaining_seconds)}s left`}</strong><span>Orders served: <b>{view.metrics.orders_fulfilled}</b></span><span>Expired: {view.metrics.orders_expired}</span><span>Incorrect: {view.metrics.incorrect_deliveries}</span><span>Waste: {view.metrics.food_wasted} · Burns: {view.metrics.burns}</span></div>
   <svg className="cooking-board" viewBox="-6 -5 12 10" role="img" aria-label={`Kitchen actor holding ${view.held}. Pot ${view.pot.stage}.`}>
    <rect x="-5.8" y="-4.7" width="11.6" height="9.4" rx=".4" fill="#e5efeb" />
    {COOKING.stations.map(station=><g key={station.id}><rect x={station.x-1.45} y={station.z-.75} width="2.9" height="1.5" rx=".2" fill={selected===station.id?'#c5debd':'#fff'} stroke="#59796d" strokeWidth=".04"/><text x={station.x} y={station.z-.1} textAnchor="middle">{station.id+1}. {station.name}</text><text x={station.x} y={station.z+.4} textAnchor="middle" className="cooking-station-detail">{station.id===1?`chop ${view.chop_ticks}/${view.chop_required_ticks}`:station.id===2?`pot: ${view.pot.stage}`:station.id===4?'serve soup':station.id===5?'discard food':''}</text></g>)}
    {ghostView&&<g className="cooking-ghost-actor"><circle cx={ghostView.actor.x} cy={ghostView.actor.z} r=".45"/><text x={ghostView.actor.x} y={ghostView.actor.z-.65} textAnchor="middle">GHOST</text></g>}
    <circle cx={view.actor.x} cy={view.actor.z} r=".38" fill="#de7048" stroke="#572e22" strokeWidth=".06"/>
    <text x={view.actor.x} y={view.actor.z+.8} textAnchor="middle">{view.held==='empty'?'you':view.held}</text>
   </svg>
   <div className="cooking-status"><strong>Holding: {view.held}</strong><span>Pot: {view.pot.stage}{view.pot.stage==='cooking'?` · ${Math.min(view.pot.ticks/view.cook_required_ticks*100,100).toFixed(0)}%`:''}</span><span>Order: tomato soup · {view.orders[0]?`${Math.ceil(view.orders[0].remaining_seconds)}s until expiry`:'no orders'}</span></div>
   {ghostView&&<div className="cooking-ghost-summary" role="status"><strong>{source?.label} · {ghostView.metrics.orders_fulfilled} orders · You {view.metrics.orders_fulfilled}</strong><span>{progress(ghostView)}</span><span>{ghost.current?.ended&&ghostView.phase!=='finished'?'Partial coverage ended: ghost frozen; no orders extrapolated.':ghostView.phase==='finished'?'Ghost shift complete.':phase==='paused'?'Both kitchens paused.':`Recorded simulation time ${ghostView.game_time_seconds.toFixed(1)}s`}</span></div>}
   {phase!=='playing'&&<div className="cooking-session"><h2>{phase==='ready'?'Ready for your shift?':phase==='paused'?'Kitchen paused':isPlayback.current?'Recording ended':'Shift complete'}</h2><p>{phase==='ended'?`${view.metrics.orders_fulfilled} served · ${view.metrics.orders_expired} expired · ${view.metrics.food_wasted} wasted`:'Select a station, then hold Work / E to use it. Your cook walks there automatically.'}</p><div className="cooking-buttons"><button disabled={busy||saving} onClick={phase==='paused'?resume:start}>{phase==='paused'?'Resume':phase==='ready'?'Start shift':'Restart shift'}</button>{phase==='ended'&&!saved&&!isPlayback.current&&<button disabled={saving} onClick={()=>void save()}>{saving?'Verifying…':'Save verified shift'}</button>}{saved&&<span>Recording: {saved}</span>}</div></div>}
   <div className="cooking-buttons cooking-controls">{COOKING.stations.map(station=><button key={station.id} disabled={phase!=='playing'||isPlayback.current} aria-pressed={selected===station.id} onClick={()=>select(station.id)}>{station.id+1} · {station.name}</button>)}<button disabled={phase!=='playing'||isPlayback.current} aria-pressed={workButton} onPointerDown={event=>{event.preventDefault();working.current=true;setWorkButton(true);area.current?.focus();}} onKeyDown={event=>{if(event.key===' '||event.key==='Enter'){working.current=true;setWorkButton(true);}}} onKeyUp={()=>{working.current=false;setWorkButton(false);}}>Hold to work (E / Space)</button>{phase==='playing'&&<button onClick={pause}>Pause (Esc)</button>}</div>
  </section>
  <section className="cooking-panel"><h2>Soup in six stations</h2><p><b>1 Pantry</b> pick tomato → <b>2 Chop</b> work 2 seconds → <b>3 Hob</b> cook 3 seconds, then retrieve → <b>4 Plates</b> plate soup → <b>5 Delivery</b> serve. <b>6 Bin</b> discards mistakes. Food left too long on the hob burns.</p><p>Keys 1–6 select a station. Hold E or Space to work; click the kitchen first. On-screen station and Work buttons do the same. Human and model controls are sampled once per simulation second. Escape, a hidden tab, or leaving the window pauses both kitchens.</p></section>
  <section className="cooking-panel"><h2>Let a text model cook</h2><p>Models see structured kitchen state. Simulation pauses during inference; latency is reported separately. Only loaded LM Studio models can run.</p><label htmlFor="cooking-model">Loaded model</label><div className="cooking-buttons"><select id="cooking-model" value={model} onChange={event=>setModel(event.target.value)}><option value="">Choose model</option>{models.map(m=><option key={m.id} value={m.id} disabled={!m.loaded}>{m.bare_id}{!m.loaded?' · unloaded':''}</option>)}</select><button disabled={!model||launching} onClick={()=>void launch()}>{launching?'Queuing…':'Run model'}</button><button onClick={()=>void refreshModels()}>Refresh models</button></div></section>
  <section className="cooking-panel"><h2>Recorded shifts</h2>{recordsError&&<p role="status" className="cooking-error">{recordsError}</p>}<div className="cooking-table"><table><thead><tr><th>Controller</th><th>Outcome</th><th>Orders served / expired</th><th>Incorrect / waste / burns</th><th>Inference</th><th>Recording</th></tr></thead><tbody>{episodes.map(episode=>{const m=episode.result_json.metrics;const measured=Number.isFinite(m?.orders_fulfilled);return <tr key={episode.episode_id}><td>{episode.controller==='calibration'?'SCRIPTED reference':episode.controller==='human'?'Human':episode.model}<small>{episode.episode_id}</small></td><td>{episode.result_json.status??episode.state}{episode.failure_reason&&<small>{episode.failure_reason}</small>}</td><td>{measured?`${m!.orders_fulfilled} / ${m!.orders_expired}`:'—'}</td><td>{measured?`${m!.incorrect_deliveries} / ${m!.food_wasted} / ${m!.burns}`:'—'}</td><td>{episode.controller==='model'&&episode.result_json.inference_seconds!=null?`${episode.result_json.inference_seconds.toFixed(2)}s`:'—'}</td><td>{episode.result_json.status?<><button disabled={locked} onClick={()=>void watch(episode)}>Watch</button>{['model','calibration'].includes(episode.controller)&&<button disabled={locked} onClick={()=>void chooseGhost(episode.episode_id)}>Challenge ghost</button>} <a href={`/api/gameplay/cooking/episodes/${episode.episode_id}/replay`} target="_blank" rel="noreferrer">Replay JSON</a></>:['pending','running'].includes(episode.state)?<button onClick={()=>void request(`/episodes/${episode.episode_id}/cancel`,{method:'POST'}).then(refresh).catch(e=>setError(e.message))}>Cancel</button>:'—'}</td></tr>;})}{!episodes.length&&<tr><td colSpan={6}>No shifts recorded. Play a shift, launch a model, or create the SCRIPTED reference.</td></tr>}</tbody></table></div><p>Ghosts are non-interacting replay actors, not live opponents. Partial recordings stop at their recorded coverage. Kitchen resources and orders are independent; scores compare orders served in simulation time.</p></section>
 </main>;
}

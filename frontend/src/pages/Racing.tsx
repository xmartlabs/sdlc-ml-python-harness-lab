import { useEffect, useRef, useState } from 'react';
import { ACTION_TICKS, MAX_TICKS, TRACK, TICK_RATE, createRace, stepRace, observeRace, isTerminal } from '../gameplay/racing.mjs';
import type { RaceControls, RaceObservation, RaceMetrics } from '../gameplay/racing.mjs';
import { SESSION_EXPIRED_EVENT } from '../api';
import { createRacingScene } from '../gameplay/racingScene';
import './Racing.css';

type Model = { id: string; bare_id: string; loaded: boolean; quantization: string };
type Episode = { episode_id: string; controller: string; model: string; state: string; failure_reason: string; result_json: { metrics?: RaceMetrics; inference_seconds?: number; status?: string } };
async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api/gameplay/racing${path}`, { credentials: 'same-origin', ...options, headers: { 'Content-Type': 'application/json', ...options?.headers } });
  if (response.status === 401) window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));
  if (!response.ok) { const error = await response.json().catch(() => ({})); throw new Error(typeof error.detail === 'string' ? error.detail : 'Request failed. Check the API and your session.'); }
  return response.json();
}
const ZERO = { throttle: 0, brake: 0, steer: 0, reset: 0 };

export function Racing() {
  const canvas = useRef<HTMLCanvasElement>(null), state = useRef(createRace(7));
  const actions = useRef<RaceControls[]>([]), pressed = useRef(new Set<string>());
  const playing = useRef(false), paused = useRef(false);
  const recording = useRef<RaceControls[] | null>(null);
  const [view, setView] = useState<RaceObservation>(() => observeRace(state.current));
  const [phase, setPhase] = useState<'ready'|'playing'|'paused'|'ended'>('ready');
  const [renderError, setRenderError] = useState(''), [error, setError] = useState('');
  const [saved, setSaved] = useState(''), [saving, setSaving] = useState(false), [launching, setLaunching] = useState(false);
  const [models, setModels] = useState<Model[]>([]), [model, setModel] = useState(''), [episodes, setEpisodes] = useState<Episode[]>([]);
  const [recordsError, setRecordsError] = useState('');
  const refresh = () => request<Episode[]>('/episodes').then(setEpisodes).catch(e => setRecordsError(e.message));
  useEffect(() => {
    void refresh();
    void request<Model[]>('/models').then(items => { setModels(items); setModel(items.find(m => m.loaded)?.id ?? ''); }).catch(e => setRecordsError(e.message));
    const timer = window.setInterval(() => void refresh(), 3000); return () => clearInterval(timer);
  }, []);
  useEffect(() => {
    if (!canvas.current) return;
    let scene: ReturnType<typeof createRacingScene>;
    try { scene = createRacingScene(canvas.current); } catch { setRenderError('WebGL could not start. Enable hardware acceleration to play; saved numerical results remain available.'); return; }
    let animation = 0, previous = 0, accumulator = 0, intervalRemaining = 0, controls: RaceControls = ZERO;
    const stopKeys = () => { pressed.current.clear(); if (playing.current) { paused.current = true; setPhase('paused'); } };
    const keyboard = (event: KeyboardEvent) => {
      if (event.type === 'keyup') pressed.current.delete(event.key.toLowerCase());
      if (document.activeElement !== canvas.current) return;
      if (['ArrowUp','ArrowDown','ArrowLeft','ArrowRight','w','a','s','d','r','Escape',' '].includes(event.key)) event.preventDefault();
      if (event.type === 'keydown') pressed.current.add(event.key.toLowerCase()); else pressed.current.delete(event.key.toLowerCase());
      if (event.type === 'keydown' && event.key === 'Escape') { paused.current = true; pressed.current.clear(); setPhase('paused'); }
    };
    const gameCanvas = canvas.current; gameCanvas.addEventListener('blur', stopKeys);
    window.addEventListener('keydown', keyboard); window.addEventListener('keyup', keyboard); window.addEventListener('blur', stopKeys);
    document.addEventListener('visibilitychange', stopKeys);
    let lastTick = -1;
    const frame = (now: number) => {
      if (playing.current && !paused.current) {
        accumulator += previous ? Math.min((now - previous) / 1000, .1) : 0;
        while (accumulator >= 1 / TICK_RATE && !isTerminal(state.current)) {
          if (state.current.tick === 0) intervalRemaining = 0;
          if (intervalRemaining === 0) {
            const keys = pressed.current;
            controls = { throttle: keys.has('w') || keys.has('arrowup') ? 1 : 0, brake: keys.has('s') || keys.has('arrowdown') || keys.has(' ') ? 1 : 0,
              steer: (keys.has('d') || keys.has('arrowright') ? 1 : 0) - (keys.has('a') || keys.has('arrowleft') ? 1 : 0), reset: keys.has('r') ? 1 : 0 };
            if (recording.current) { const recorded = recording.current[actions.current.length]; if (!recorded) { playing.current = false; setPhase('ended'); break; } controls = recorded; }
            actions.current.push(controls); intervalRemaining = ACTION_TICKS;
          }
          stepRace(state.current, controls, 1); intervalRemaining--; accumulator -= 1 / TICK_RATE;
        }
        if (isTerminal(state.current)) { playing.current = false; setPhase('ended'); pressed.current.clear(); }
      } else accumulator = 0;
      previous = now;
      const observation = observeRace(state.current);
      if (lastTick !== observation.tick && (observation.tick % 6 === 0 || isTerminal(state.current))) { setView(observation); lastTick = observation.tick; }
      scene.render(observation, !playing.current && !isTerminal(state.current));
      animation = requestAnimationFrame(frame);
    };
    animation = requestAnimationFrame(frame);
    return () => { gameCanvas.removeEventListener('blur', stopKeys); cancelAnimationFrame(animation); scene.dispose(); window.removeEventListener('keydown', keyboard); window.removeEventListener('keyup', keyboard); window.removeEventListener('blur', stopKeys); document.removeEventListener('visibilitychange', stopKeys); };
  }, []);
  const start = () => { recording.current = null; state.current = createRace(7); actions.current = []; pressed.current.clear(); paused.current = false; playing.current = true; setSaved(''); setError(''); setView(observeRace(state.current)); setPhase('playing'); canvas.current?.focus(); };
  const watch = async (episode: Episode) => { try { const replay = await request<{ config: { seed: number }; frames: { action: { controls: RaceControls } | null }[] }>(`/episodes/${episode.episode_id}/replay`); state.current = createRace(replay.config.seed); actions.current = []; recording.current = replay.frames.filter(f => f.action).map(f => f.action!.controls); pressed.current.clear(); paused.current = false; playing.current = true; setSaved(episode.episode_id); setPhase('playing'); canvas.current?.focus(); } catch(e) { setError((e as Error).message); } };
  const resume = () => { paused.current = false; setPhase('playing'); canvas.current?.focus(); };
  const save = async () => { setSaving(true); setError(''); try { const row = await request<Episode>('/human', { method: 'POST', body: JSON.stringify({ seed: 7, actions: actions.current }) }); if (row.state === 'failed') throw new Error(row.failure_reason); setSaved(row.episode_id); await refresh(); } catch(e) { setError((e as Error).message); } finally { setSaving(false); } };
  const launch = async () => { setLaunching(true); setError(''); try { const row = await request<Episode>('/episodes', { method: 'POST', body: JSON.stringify({seed:7, model}) }); if(row.state === 'failed') throw new Error(row.failure_reason); await refresh(); } catch(e) { setError((e as Error).message); } finally { setLaunching(false); } };
  return <div className="racing-page">
    <header className="racing-heading"><div><p className="race-eyebrow">GAMEPLAY LAB / 01</p><h1>Tidal Ring<span>Time trial</span></h1><p>One island. Twelve gates. Find your cleanest line.</p></div><div className="race-spec">{Math.round(TRACK.length)}m circuit<span>Original course · rules v{TRACK.version} · seed 7</span></div></header>
    <section className="race-stage" aria-label="Tidal Ring racing game">
      <canvas ref={canvas} tabIndex={0} aria-label="Racing game. W or up accelerate, S or down brake, A and D steer, R recover, Escape pause." />
      <div className="race-hud"><div><span>RACE TIME</span><strong>{view.game_time_seconds.toFixed(2)}<small>s</small></strong></div><div><span>CHECKPOINTS</span><strong>{view.checkpoints_passed}<small> / {view.checkpoints_total}</small></strong></div><div><span>SPEED</span><strong>{Math.round(view.speed * 3.6)}<small>km/h</small></strong></div></div>
      <div className="race-progress" aria-label={`Course progress ${Math.round(view.progress*100)} percent`}><i style={{width:`${view.progress*100}%`}} /></div>
      {view.phase === 'countdown' && phase === 'playing' && <div className="race-countdown" role="status">{Math.ceil(view.countdown_seconds) || 'GO'}</div>}
      {(phase === 'ready' || phase === 'paused' || phase === 'ended' || renderError) && <div className="race-overlay"><div className="race-start-card"><p className="race-eyebrow">{phase === 'ended' ? 'SESSION COMPLETE' : phase === 'paused' ? 'TAKE A BREATH' : 'THE ISLAND IS YOURS'}</p><h2>{renderError ? 'Renderer unavailable' : phase === 'ended' ? view.phase === 'finished' ? 'Across the line.' : view.phase === 'timeout' ? 'Time is up.' : 'Attempt ended.' : phase === 'paused' ? 'Race paused' : 'Take the wheel.'}</h2><p>{renderError || (phase === 'ended' ? `${view.checkpoints_passed} gates · ${view.metrics.collisions} impacts · ${view.metrics.resets} recoveries` : 'A fixed arcade time trial for humans and text models. Pass every checkpoint in order; the clock rewards a clean lap.')}</p><div className="race-buttons">{!renderError && <button className="race-primary" onClick={phase === 'paused' ? resume : start}>{phase === 'ready' ? 'Start time trial' : phase === 'paused' ? 'Resume race' : 'Race again'} <span>↗</span></button>}{phase === 'ended' && !saved && <button onClick={() => void save()} disabled={saving}>{saving ? 'Verifying…' : 'Save verified run'}</button>}{saved && <span role="status">Run saved</span>}</div></div></div>}
      <div className="race-stage-footer"><span>TIDAL / RING</span><span>{phase === 'playing' ? 'ESC to pause · click game to focus' : 'Fixed 60 Hz simulation · no live opponents'}</span>{phase === 'playing' && <button onClick={() => {paused.current = true;setPhase('paused');pressed.current.clear();}}>Pause</button>}</div>
    </section>
    <div className="race-below"><section className="race-controls"><p className="race-eyebrow">YOUR CONTROLS</p><h2>Drive the line.</h2><div><span><kbd>W</kbd><kbd>↑</kbd> Accelerate</span><span><kbd>S</kbd><kbd>↓</kbd> Brake</span><span><kbd>A</kbd><kbd>D</kbd> Steer</span><span><kbd>R</kbd> Recover at checkpoint</span></div><p>Recovery costs time. Inputs are sampled five times a second for both human and model runs. Tab into the game to drive. Switching tabs pauses your race.</p></section>
    <section className="race-model"><p className="race-eyebrow">TEXT MODEL / SAME RULES</p><h2>Give a model the wheel.</h2><p>It sees numerical road and car state. Game time pauses while it thinks; latency is measured separately.</p><label htmlFor="race-model">Loaded LM Studio model</label><div className="race-model-select"><select id="race-model" value={model} onChange={e => setModel(e.target.value)}><option value="">Choose a loaded model</option>{models.map(m => <option key={m.id} value={m.id} disabled={!m.loaded}>{m.bare_id}{!m.loaded ? ' · not loaded' : ''}</option>)}</select><button className="race-primary" disabled={!model || launching} onClick={() => void launch()}>{launching ? 'Queuing…' : 'Run model'}</button></div><button className="race-text-button" onClick={() => {void request<Model[]>('/models').then(setModels).catch(e => setRecordsError(e.message));}}>Refresh models</button></section></div>
    {error && <p className="race-error" role="alert">{error}</p>}
    <section className="race-records"><div className="race-record-title"><div><p className="race-eyebrow">RECORDED ATTEMPTS</p><h2>Every run leaves a trace.</h2></div><span>Finish time and model latency stay separate</span></div>{recordsError && <p className="race-error" role="status">{recordsError}</p>}<div className="race-table-wrap"><table><thead><tr><th>Driver</th><th>Outcome</th><th>Finish / progress</th><th>Impacts / resets</th><th>Inference</th><th>Recording</th></tr></thead><tbody>{episodes.map(e => {const m = e.result_json.metrics; return <tr key={e.episode_id}><td>{e.controller === 'human' ? 'Human' : e.model}<small>{e.episode_id}</small></td><td>{m?.finished ? 'Finished' : e.result_json.status || e.state}{e.failure_reason && <small>{e.failure_reason}</small>}</td><td>{m?.finished ? `${m.finish_time_seconds?.toFixed(2)}s` : Number.isFinite(m?.progress) ? `${Math.round(m!.progress*100)}% · DNF` : '—'}</td><td>{Number.isFinite(m?.collisions) ? `${m!.collisions} / ${m!.resets}` : '—'}</td><td>{e.controller === 'human' ? '—' : e.result_json.inference_seconds != null ? `${e.result_json.inference_seconds.toFixed(2)}s` : '—'}</td><td>{e.result_json.status ? <><button onClick={() => void watch(e)}>Watch run</button> <a href={`/api/gameplay/racing/episodes/${e.episode_id}/replay`} target="_blank" rel="noreferrer">Replay JSON ↗</a></> : ['pending', 'running'].includes(e.state) ? <button onClick={() => void request(`/episodes/${e.episode_id}/cancel`, {method:'POST'}).then(refresh).catch(err => setError(err.message))}>Cancel</button> : <span>—</span>}</td></tr>;})}{!episodes.length && <tr><td colSpan={6}>No recorded attempts yet. Finish a human time trial or launch a loaded model.</td></tr>}</tbody></table></div><p>Numerical recordings only. Human-versus-model ghost challenges arrive in the next contribution.</p></section>
  </div>;
}

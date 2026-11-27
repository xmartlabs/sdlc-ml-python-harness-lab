import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { RaceControls, RaceObservation } from "../gameplay/racing.mjs";
import { createRacingScene } from "../gameplay/racingScene";
import { createRunReplay } from "../gameplay/runReplay.mjs";
import type { RunReplay } from "../gameplay/runReplay.mjs";
import type { RacingReplayRecord } from "../types";
import { defineMessages, useMessages } from "../i18n";
import "../pages/Racing.css";

const messages = defineMessages({
  en: {
    loading: "Loading the graded lap…",
    missing: "No graded racing episode for this run.",
    renderer: "WebGL could not start. Enable hardware acceleration to watch the replay.",
    eyebrow: "REPLAY / GRADED EPISODE",
    note: "The exact controls the grader accepted, replayed with the lab's engine under the case's frozen condition.",
    play: "Play",
    pause: "Pause",
    restart: "Restart",
    speed: "Speed",
    time: "RACE TIME",
    gates: "CHECKPOINTS",
    kmh: "SPEED",
    action: "ACTION",
    controls: "CONTROLS",
    countdown: "countdown",
    finished: "Across the line",
    stopped: "Episode ended",
    result: (m: RacingReplayRecord["metrics"], par: number | null | undefined) =>
      m.finished
        ? `Lap ${m.finish_time_seconds?.toFixed(2)} s${par ? ` (par ${par} s)` : ""} · ${m.collisions} collisions · ${m.resets} resets`
        : `${Math.round((m.progress ?? 0) * 100)}% of the lap · ${m.collisions} collisions · ${m.resets} resets`,
  },
  es: {
    loading: "Cargando la vuelta calificada…",
    missing: "Este run no tiene un episodio de carrera calificado.",
    renderer: "WebGL no pudo iniciar. Activá la aceleración por hardware para ver el replay.",
    eyebrow: "REPLAY / EPISODIO CALIFICADO",
    note: "Los controles exactos que aceptó el grader, reproducidos con el motor del lab bajo la condición congelada del caso.",
    play: "Reproducir",
    pause: "Pausa",
    restart: "Reiniciar",
    speed: "Velocidad",
    time: "TIEMPO",
    gates: "CHECKPOINTS",
    kmh: "VELOCIDAD",
    action: "ACCIÓN",
    controls: "CONTROLES",
    countdown: "cuenta regresiva",
    finished: "Cruzó la meta",
    stopped: "Episodio terminado",
    result: (m: RacingReplayRecord["metrics"], par: number | null | undefined) =>
      m.finished
        ? `Vuelta ${m.finish_time_seconds?.toFixed(2)} s${par ? ` (par ${par} s)` : ""} · ${m.collisions} choques · ${m.resets} resets`
        : `${Math.round((m.progress ?? 0) * 100)}% de la vuelta · ${m.collisions} choques · ${m.resets} resets`,
  },
});

const SPEEDS = [0.5, 1, 2, 4] as const;

function formatControls(c: RaceControls | undefined): string {
  if (!c) return "—";
  const n = (v: number | undefined) => (v ?? 0).toFixed(2);
  return `T ${n(c.throttle)} · B ${n(c.brake)} · S ${n(c.steer)}${(c.reset ?? 0) >= 0.5 ? " · R" : ""}`;
}

/** Watch the graded racing episode of a run again (ADR-0103). */
export function RacingReplay({ runId }: { runId: string }) {
  const m = useMessages(messages);
  const canvas = useRef<HTMLCanvasElement>(null);
  const replay = useRef<RunReplay | null>(null);
  const playing = useRef(true);
  const speed = useRef(1);
  const [record, setRecord] = useState<RacingReplayRecord | null>(null);
  const [loadError, setLoadError] = useState("");
  const [renderError, setRenderError] = useState("");
  const [view, setView] = useState<RaceObservation | null>(null);
  const [isPlaying, setIsPlaying] = useState(true);
  const [rate, setRate] = useState(1);
  const [ended, setEnded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .getRacingReplay(runId)
      .then((data) => { if (!cancelled) setRecord(data); })
      .catch((error: Error) => { if (!cancelled) setLoadError(error.message || m.missing); });
    return () => { cancelled = true; };
  }, [runId, m.missing]);

  useEffect(() => {
    if (!record || !canvas.current) return;
    let scene: ReturnType<typeof createRacingScene>;
    try {
      scene = createRacingScene(canvas.current);
    } catch {
      setRenderError(m.renderer);
      return;
    }
    replay.current = createRunReplay(record.condition, record.controls);
    playing.current = true;
    setIsPlaying(true);
    setEnded(false);
    const tickSeconds = 1 / record.condition.tick_rate;
    let animation = 0, previous = 0, accumulator = 0, lastShown = -1;
    const frame = (now: number) => {
      const current = replay.current;
      if (current) {
        if (playing.current && !current.done()) {
          accumulator += previous ? Math.min((now - previous) / 1000, 0.1) * speed.current : 0;
          const ticks = Math.floor(accumulator / tickSeconds);
          if (ticks > 0) { current.advance(ticks); accumulator -= ticks * tickSeconds; }
          if (current.done()) { playing.current = false; setIsPlaying(false); setEnded(true); }
        } else accumulator = 0;
        const observation = current.observe();
        if (observation.tick !== lastShown && (observation.tick % 6 === 0 || current.done())) {
          setView(observation);
          lastShown = observation.tick;
        }
        scene.render(observation, false);
      }
      previous = now;
      animation = requestAnimationFrame(frame);
    };
    animation = requestAnimationFrame(frame);
    return () => { cancelAnimationFrame(animation); scene.dispose(); };
  }, [record, m.renderer]);

  const toggle = () => {
    if (!replay.current) return;
    if (replay.current.done()) return restart();
    playing.current = !playing.current;
    setIsPlaying(playing.current);
  };
  const restart = () => {
    if (!record) return;
    replay.current = createRunReplay(record.condition, record.controls);
    playing.current = true;
    setIsPlaying(true);
    setEnded(false);
  };
  const changeRate = (value: number) => { speed.current = value; setRate(value); };

  if (loadError) return <p className="muted">{loadError}</p>;
  if (!record) return <p className="muted">{m.loading}</p>;

  const index = replay.current?.actionIndex() ?? -1;
  const current = index >= 0 ? record.controls[Math.min(index, record.controls.length - 1)] : undefined;
  return (
    <div className="racing-page run-replay">
      <p className="race-eyebrow">{m.eyebrow}</p>
      <p className="muted small">{m.note} {m.result(record.metrics, record.par_seconds)}</p>
      <section className="race-stage" aria-label="Racing replay">
        <canvas ref={canvas} aria-label="Replay of the graded racing episode" />
        {view ? (
          <div className="race-hud">
            <div><span>{m.time}</span><strong>{view.game_time_seconds.toFixed(2)}<small>s</small></strong></div>
            <div><span>{m.gates}</span><strong>{view.checkpoints_passed}<small> / {view.checkpoints_total}</small></strong></div>
            <div><span>{m.kmh}</span><strong>{Math.round(view.speed * 3.6)}<small>km/h</small></strong></div>
            <div><span>{m.action}</span><strong>{index < 0 ? "—" : Math.min(index + 1, record.controls.length)}<small> / {record.controls.length}</small></strong></div>
          </div>
        ) : null}
        {view ? <div className="race-progress"><i style={{ width: `${view.progress * 100}%` }} /></div> : null}
        {view?.phase === "countdown" ? (
          <div className="race-countdown" role="status">{Math.ceil(view.countdown_seconds) || "GO"}</div>
        ) : null}
        {renderError ? <div className="race-overlay"><div className="race-start-card"><p>{renderError}</p></div></div> : null}
        <div className="race-stage-footer">
          <span>{ended ? (view?.phase === "finished" ? m.finished : m.stopped) : `${m.controls}: ${index < 0 ? m.countdown : formatControls(current)}`}</span>
          <span />
        </div>
      </section>
      <div className="run-replay-controls">
        <button className="race-primary" onClick={toggle} type="button">{isPlaying ? m.pause : ended ? m.restart : m.play}</button>
        <button onClick={restart} type="button">{m.restart}</button>
        <span className="muted small">{m.speed}</span>
        {SPEEDS.map((value) => (
          <button aria-pressed={rate === value} className={rate === value ? "active" : ""} key={value} onClick={() => changeRate(value)} type="button">{value}×</button>
        ))}
      </div>
    </div>
  );
}

import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { defineMessages, useMessages } from "../i18n";
import type { RunPhaseProgress, RunProgress } from "../types";
import { ErrorBanner } from "./ErrorBanner";

// What a long run is doing right now (ADR-0080): its phases, how long each
// took, what each cost, how far each got, what the agent last did and how long
// ago — and, when a phase runs past its expected time, the choice to keep
// waiting or to stop it and grade what it built.

type Props = {
  runId: string;
  live: boolean;
  onStopped?: () => void;
};

const MESSAGES = defineMessages({
  en: {
    title: "Progress",
    phase: (n: number, total: number | null) => (total ? `Phase ${n}/${total}` : `Step ${n}`),
    running: "running",
    done: "done",
    pending: "pending",
    lastAction: "Last action",
    quiet: (s: string) => `quiet for ${s}`,
    cost: "Cost",
    costPartial: "so far (the running phase adds its cost when it ends)",
    grade: (p: number, t: number) => `${p}/${t} checks`,
    grading: "grading…",
    unmeasured: "not graded",
    slowTitle: (n: number) => `Phase ${n} is slower than expected`,
    slowBody: (took: string, expected: string, decide: string, stop: boolean) =>
      `It has run ${took} against ${expected} expected. ` +
      (stop
        ? `With no answer in ${decide} it will be stopped and graded on what it built.`
        : `With no answer in ${decide} it keeps running.`),
    keepWaiting: (m: number) => `Keep waiting (+${m} min)`,
    stopAndGrade: "Stop and grade",
    stopConfirm: "Stop this run now and grade the work it has done so far?",
    granted: (m: number) => `${m} min of extra time granted to this run.`
  },
  es: {
    title: "Progreso",
    phase: (n: number, total: number | null) => (total ? `Fase ${n}/${total}` : `Paso ${n}`),
    running: "en curso",
    done: "lista",
    pending: "pendiente",
    lastAction: "Última acción",
    quiet: (s: string) => `sin actividad hace ${s}`,
    cost: "Costo",
    costPartial: "hasta ahora (la fase en curso suma su costo al terminar)",
    grade: (p: number, t: number) => `${p}/${t} chequeos`,
    grading: "calificando…",
    unmeasured: "sin calificar",
    slowTitle: (n: number) => `La fase ${n} va más lenta de lo esperado`,
    slowBody: (took: string, expected: string, decide: string, stop: boolean) =>
      `Lleva ${took} contra ${expected} esperados. ` +
      (stop
        ? `Si no hay respuesta en ${decide}, se corta y se califica lo que construyó.`
        : `Si no hay respuesta en ${decide}, sigue corriendo.`),
    keepWaiting: (m: number) => `Seguir esperando (+${m} min)`,
    stopAndGrade: "Cortar y calificar",
    stopConfirm: "¿Cortar esta corrida ahora y calificar lo que hizo hasta acá?",
    granted: (m: number) => `${m} min extra concedidos a esta corrida.`
  }
});

function duration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) {
    return "—";
  }
  const total = Math.max(0, Math.round(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return `${h} h ${m} min`;
  if (m > 0) return `${m} min${s && m < 10 ? ` ${s} s` : ""}`;
  return `${s} s`;
}

function money(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : `US$${value.toFixed(2)}`;
}

function PhaseCell({ row, current }: { row: RunPhaseProgress; current: boolean }) {
  const m = useMessages(MESSAGES);
  const state = row.done ? "done" : current ? "running" : "pending";
  const grade = row.grade;
  let gradeText: string | null = null;
  if (grade && typeof grade.passed === "number" && typeof grade.total === "number") {
    gradeText = m.grade(grade.passed, grade.total);
  } else if (grade && (grade.unmeasured || grade.error)) {
    gradeText = m.unmeasured;
  } else if (row.done) {
    gradeText = m.grading;
  }
  const ratio =
    grade && typeof grade.passed === "number" && grade.total ? grade.passed / grade.total : null;
  return (
    <li className={`run-phase run-phase--${state}`} title={row.name}>
      <div className="run-phase-head">
        <span className="run-phase-label">{m.phase(row.phase, row.phase_total)}</span>
        <span className="run-phase-state">{m[state]}</span>
      </div>
      <div className="run-phase-name">{row.name || " "}</div>
      <div className="run-phase-stats">
        <span>{duration(row.seconds)}</span>
        <span>{row.cost_usd !== null ? money(row.cost_usd) : `${row.turns} turns`}</span>
      </div>
      {gradeText ? (
        <div className="run-phase-grade">
          {ratio !== null ? (
            <span className="run-phase-bar" aria-hidden="true">
              <span style={{ width: `${Math.round(ratio * 100)}%` }} />
            </span>
          ) : null}
          <span>{gradeText}</span>
        </div>
      ) : null}
    </li>
  );
}

export function RunProgressPanel({ runId, live, onStopped }: Props) {
  const m = useMessages(MESSAGES);
  const [progress, setProgress] = useState<RunProgress | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api
      .getRunProgress(runId)
      .then((result) => {
        setProgress(result);
        setError(null);
      })
      .catch(() => {
        // A run that never wrote a step has no progress to show yet.
      });
  }, [runId]);

  useEffect(() => {
    load();
    if (!live) {
      return undefined;
    }
    const timer = window.setInterval(load, 5000);
    return () => window.clearInterval(timer);
  }, [load, live]);

  if (!progress || progress.steps.length === 0) {
    return null;
  }

  const total = progress.phase_total ?? progress.steps.length;
  const rows: RunPhaseProgress[] = [...progress.steps];
  // Phases not reached yet, so the strip always shows the whole journey.
  for (let n = rows.length + 1; total && n <= total; n += 1) {
    rows.push({
      step: n,
      phase: n,
      phase_total: total,
      name: "",
      done: false,
      seconds: null,
      turns: 0,
      output_tokens: 0,
      cost_usd: null,
      last_action: null,
      last_event_at: null,
      grade: null
    });
  }
  const slow = live ? progress.slow : null;
  const now = Date.now() / 1000;
  const extendMinutes = Math.round((progress.extend_seconds || 1800) / 60);

  const keepWaiting = () => {
    setBusy(true);
    api
      .keepWaiting(runId)
      .then(() => {
        setNotice(m.granted(extendMinutes));
        load();
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setBusy(false));
  };

  const stopAndGrade = () => {
    if (!window.confirm(m.stopConfirm)) {
      return;
    }
    setBusy(true);
    api
      .cancelRun(runId)
      .then(() => {
        load();
        onStopped?.();
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setBusy(false));
  };

  return (
    <section className="run-progress" aria-label={m.title}>
      {error ? <ErrorBanner message={error} onDismiss={() => setError(null)} /> : null}
      {slow ? (
        <div className="run-progress-slow" role="alert">
          <div>
            <strong>{m.slowTitle(slow.step)}</strong>
            <p>
              {m.slowBody(
                duration(slow.expected_seconds + slow.extra_seconds + (now - slow.slow_since)),
                duration(slow.expected_seconds + slow.extra_seconds),
                duration(Math.max(0, slow.decide_by - now)),
                slow.on_slow === "stop"
              )}
            </p>
          </div>
          <div className="run-actions">
            <button disabled={busy} onClick={keepWaiting} type="button">
              {m.keepWaiting(extendMinutes)}
            </button>
            <button className="danger" disabled={busy} onClick={stopAndGrade} type="button">
              {m.stopAndGrade}
            </button>
          </div>
        </div>
      ) : null}
      {notice ? <p className="muted small">{notice}</p> : null}
      <ol className="run-phases">
        {rows.map((row) => (
          <PhaseCell
            current={live && row.step === progress.current_step}
            key={row.step}
            row={row}
          />
        ))}
      </ol>
      <div className="run-progress-foot">
        {live && progress.last_action ? (
          <span className="run-progress-action">
            <span className="muted">{m.lastAction}:</span> <code>{progress.last_action}</code>
            {progress.quiet_seconds !== null ? (
              <span
                className={
                  progress.quiet_seconds > 600 ? "run-progress-quiet warn" : "run-progress-quiet"
                }
              >
                {" "}
                · {m.quiet(duration(progress.quiet_seconds))}
              </span>
            ) : null}
          </span>
        ) : (
          <span />
        )}
        <span>
          <span className="muted">{m.cost}:</span> {money(progress.cost_usd)}
          {!progress.cost_complete && live ? (
            <span className="muted small"> {m.costPartial}</span>
          ) : null}
        </span>
      </div>
    </section>
  );
}

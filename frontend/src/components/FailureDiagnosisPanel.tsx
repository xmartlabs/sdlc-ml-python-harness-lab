import { useEffect, useState } from "react";

import { api } from "../api";
import { defineMessages, useLang, useMessages } from "../i18n";
import type { FailureDiagnosis, FailureEvidence } from "../types";
import { ErrorBanner } from "./ErrorBanner";
import { Markdown } from "./Markdown";

const MESSAGES = defineMessages({
  en: {
    title: "Why it failed",
    explainer:
      "The facts the failure turns on, read from the run's event stream: how " +
      "long each recent command ran, the command the run was in when it " +
      "stopped, and whether the provider refused anything.",
    usageLimit: "Usage limit",
    usageUnknown: "not reported",
    overage: "using overage",
    apiErrors: "API errors",
    none: "none",
    duration: "Ran for",
    minutes: (value: number) => `${value} min`,
    stallBudget: "Silence allowed in a command",
    longestWait: "Longest wait on one command",
    backgrounded: "Claude Code moved it to the background: a long command, not a hung one",
    neverAnswered: "never answered",
    openCommand: "Command in progress when it stopped",
    since: (seconds: number) => `for ${formatSeconds(seconds)}`,
    recent: "Recent commands",
    command: "Command",
    took: "Took",
    output: "End of output",
    noAnswer: "no answer",
    steps: "Steps",
    stepLine: (step: number, finished: boolean, turns: number | null) =>
      `step ${step}: ${finished ? "finished" : "did not finish"}` +
      (turns ? `, ${turns} turns` : ""),
    logTail: "End of the step log",
    analyze: "Analyze with AI",
    reanalyze: "Analyze again",
    analyzing: "Analyzing…",
    analyzeNote:
      "One model call over the facts above (never the raw log). The answer is " +
      "kept with the run; it is advice, and changes no score.",
    by: (model: string) => `Reading by ${model}`,
    loadFailed: "Could not read the run's evidence."
  },
  es: {
    title: "Por qué falló",
    explainer:
      "Los datos de los que depende la falla, leídos del registro de eventos " +
      "de la corrida: cuánto tardó cada comando reciente, en qué comando estaba " +
      "cuando se detuvo y si el proveedor rechazó algo.",
    usageLimit: "Límite de uso",
    usageUnknown: "sin datos",
    overage: "usando excedente",
    apiErrors: "Errores de API",
    none: "ninguno",
    duration: "Duró",
    minutes: (value: number) => `${value} min`,
    stallBudget: "Silencio permitido en un comando",
    longestWait: "Espera más larga en un comando",
    backgrounded: "Claude Code lo pasó a segundo plano: un comando largo, no uno colgado",
    neverAnswered: "nunca respondió",
    openCommand: "Comando en curso cuando se detuvo",
    since: (seconds: number) => `desde hacía ${formatSeconds(seconds)}`,
    recent: "Comandos recientes",
    command: "Comando",
    took: "Tardó",
    output: "Final de la salida",
    noAnswer: "sin respuesta",
    steps: "Pasos",
    stepLine: (step: number, finished: boolean, turns: number | null) =>
      `paso ${step}: ${finished ? "terminó" : "no terminó"}` +
      (turns ? `, ${turns} turnos` : ""),
    logTail: "Final del log del paso",
    analyze: "Analizar con IA",
    reanalyze: "Analizar de nuevo",
    analyzing: "Analizando…",
    analyzeNote:
      "Una llamada a un modelo con los datos de arriba (nunca el log crudo). " +
      "La respuesta queda guardada con la corrida; es un consejo y no cambia " +
      "ningún puntaje.",
    by: (model: string) => `Lectura de ${model}`,
    loadFailed: "No se pudo leer la evidencia de la corrida."
  }
});

function formatSeconds(seconds: number): string {
  if (seconds < 90) {
    return `${Math.round(seconds)} s`;
  }
  return `${Math.round(seconds / 6) / 10} min`;
}

// A command that ran this long is worth pointing at: it is the silence the
// stall clock measures.
const LONG_COMMAND_SECONDS = 120;

type Props = {
  runId: string;
  // Changes when a retried run fails again, so the panel reloads.
  failureKey: string;
};

export function FailureDiagnosisPanel({ runId, failureKey }: Props) {
  const m = useMessages(MESSAGES);
  const { lang } = useLang();
  const [evidence, setEvidence] = useState<FailureEvidence | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [diagnosis, setDiagnosis] = useState<FailureDiagnosis | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setEvidence(null);
    setLoadError(false);
    api
      .failureEvidence(runId)
      .then((value) => active && setEvidence(value))
      .catch(() => active && setLoadError(true));
    return () => {
      active = false;
    };
  }, [runId, failureKey]);

  useEffect(() => {
    let active = true;
    setDiagnosis(null);
    api
      .failureDiagnosis(runId, lang)
      .then((value) => active && setDiagnosis(value))
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, [runId, failureKey, lang]);

  const analyze = (refresh: boolean) => {
    setBusy(true);
    setError(null);
    api
      .diagnoseFailure(runId, lang, refresh)
      .then(setDiagnosis)
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : String(reason)))
      .finally(() => setBusy(false));
  };

  if (loadError) {
    return (
      <div className="panel subtle">
        <span className="muted">{m.loadFailed}</span>
      </div>
    );
  }
  if (evidence === null) {
    return null;
  }

  const usage = evidence.usage_limit;
  const open = evidence.open_command;
  const wait = evidence.longest_wait;
  // Worth its own line only when it is long and not the same call as `open`.
  const showWait = wait !== null && wait.seconds >= LONG_COMMAND_SECONDS;
  return (
    <div className="panel subtle failure-diagnosis">
      <div className="panel-head">
        <h3>{m.title}</h3>
        <span className="muted">{m.explainer}</span>
      </div>
      <div className="detail-grid">
        <div>
          <span>{m.usageLimit}</span>
          <strong className={usage?.status === "rejected" ? "error-text" : undefined}>
            {usage ? `${usage.status}${usage.overage ? ` (${m.overage})` : ""}` : m.usageUnknown}
          </strong>
        </div>
        <div>
          <span>{m.apiErrors}</span>
          <strong className={evidence.api_errors.length ? "error-text" : undefined}>
            {evidence.api_errors.length
              ? evidence.api_errors.map((e) => `${e.status ?? "?"}: ${e.message}`).join(" · ")
              : m.none}
          </strong>
        </div>
        <div>
          <span>{m.duration}</span>
          <strong>
            {evidence.run.duration_minutes !== null ? m.minutes(evidence.run.duration_minutes) : "—"}
          </strong>
        </div>
        <div>
          <span>{m.stallBudget}</span>
          <strong>
            {evidence.run.limits.stall_seconds > 0
              ? formatSeconds(evidence.run.limits.stall_seconds)
              : "—"}
          </strong>
        </div>
        <div>
          <span>{m.steps}</span>
          <strong>
            {evidence.steps.length
              ? evidence.steps.map((s) => m.stepLine(s.step, s.finished, s.turns)).join(" · ")
              : "—"}
          </strong>
        </div>
      </div>
      {showWait && wait ? (
        <p>
          <span className="muted">{m.longestWait}</span>{" "}
          <strong className="error-text">{formatSeconds(wait.seconds)}</strong>{" "}
          {wait.backgrounded ? (
            <span className="muted">({m.backgrounded})</span>
          ) : !wait.answered ? (
            <span className="muted">({m.neverAnswered})</span>
          ) : null}
          <code className="failure-command">{wait.command}</code>
        </p>
      ) : null}
      {open ? (
        <p>
          <span className="muted">{m.openCommand}</span>{" "}
          {open.seconds !== null ? <span className="muted">({m.since(open.seconds)})</span> : null}
          <code className="failure-command">{open.command}</code>
        </p>
      ) : null}
      {evidence.recent_commands.length ? (
        <>
          <h4>{m.recent}</h4>
          <div className="table-scroll">
            <table className="data-table failure-commands">
              <thead>
                <tr>
                  <th>{m.command}</th>
                  <th>{m.took}</th>
                  <th>{m.output}</th>
                </tr>
              </thead>
              <tbody>
                {evidence.recent_commands.map((call, index) => (
                  <tr key={index}>
                    <td>
                      <code>{call.command}</code>
                    </td>
                    <td className={call.seconds !== null && call.seconds >= LONG_COMMAND_SECONDS ? "error-text" : undefined}>
                      {call.seconds !== null ? formatSeconds(call.seconds) : m.noAnswer}
                    </td>
                    <td>
                      <code>{call.result ?? ""}</code>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : evidence.log_tail ? (
        <>
          <h4>{m.logTail}</h4>
          <pre className="failure-log-tail">{evidence.log_tail}</pre>
        </>
      ) : null}
      <div className="panel-actions">
        <button className="ghost" disabled={busy} onClick={() => analyze(diagnosis !== null)} type="button">
          {busy ? m.analyzing : diagnosis ? m.reanalyze : m.analyze}
        </button>
        <span className="muted">{m.analyzeNote}</span>
      </div>
      {error ? <ErrorBanner message={error} onDismiss={() => setError(null)} /> : null}
      {diagnosis ? (
        <div className="failure-reading">
          <span className="muted">{m.by(diagnosis.model)}</span>
          <Markdown>{diagnosis.text}</Markdown>
        </div>
      ) : null}
    </div>
  );
}

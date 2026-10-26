import { useCallback, useEffect, useState } from "react";

import { api } from "../api";
import { defineMessages, pickMessages, useMessages } from "../i18n";
import type { HealthCheck, HealthReport } from "../types";

const REFRESH_INTERVAL_MS = 30000;

const MESSAGES = defineMessages({
  en: {
    statusLabel: {
      ok: "OK",
      warn: "Attention",
      error: "Failing",
      unknown: "Unknown"
    } as Record<HealthCheck["status"], string>,
    healLabel: {
      skills_sync: "Re-sync skill views",
      materialize_staged_skills: "Rebuild staging tree",
      refresh_models: "Refresh model catalog",
      reconcile_runs: "Reconcile stuck runs",
      build_runner_image: "Build runner image"
    } as Record<string, string>,
    title: "System health",
    refresh: "Refresh",
    intro:
      "What the lab can reach and where its generated state has drifted. Refreshes " +
      "every 30 seconds. Repairs are a fixed set of actions — there is no generic " +
      "command runner here.",
    checking: "Checking…",
    noHealthData: "No health data.",
    working: "Working…",
    ms: (value: number) => `${value} ms`,
    seconds: (value: string) => `${value} s`
  },
  es: {
    statusLabel: {
      ok: "OK",
      warn: "Atención",
      error: "Falla",
      unknown: "Desconocido"
    } as Record<HealthCheck["status"], string>,
    healLabel: {
      skills_sync: "Re-sincronizar vistas de skills",
      materialize_staged_skills: "Reconstruir árbol de staging",
      refresh_models: "Actualizar catálogo de modelos",
      reconcile_runs: "Reconciliar corridas trabadas",
      build_runner_image: "Construir imagen del runner"
    } as Record<string, string>,
    title: "Estado del sistema",
    refresh: "Actualizar",
    intro:
      "Lo que el lab puede alcanzar y dónde su estado generado se desvió. Se " +
      "actualiza cada 30 segundos. Las reparaciones son un conjunto fijo de " +
      "acciones — acá no hay un ejecutor de comandos genérico.",
    checking: "Chequeando…",
    noHealthData: "No hay datos de estado.",
    working: "Trabajando…",
    ms: (value: number) => `${value} ms`,
    seconds: (value: string) => `${value} s`
  }
});

function formatLatency(ms: number | null): string {
  if (ms === null) {
    return "";
  }
  const m = pickMessages(MESSAGES);
  return ms < 1000 ? m.ms(ms) : m.seconds((ms / 1000).toFixed(1));
}

export function Health() {
  const m = useMessages(MESSAGES);
  const [report, setReport] = useState<HealthReport | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    return api
      .readHealth()
      .then((next) => {
        setReport(next);
        setError("");
      })
      .catch((exc: Error) => setError(exc.message))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), REFRESH_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [load]);

  const runHeal = (action: string) => {
    setBusy(action);
    setMessage("");
    api
      .heal(action)
      .then((result) => {
        setMessage(result.detail);
        return load();
      })
      .catch((exc: Error) => setError(exc.message))
      .finally(() => setBusy(""));
  };

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>{m.title}</h2>
        <div className="panel-actions">
          {report ? (
            <span className={`health-pill health-pill--${report.status}`}>
              {m.statusLabel[report.status]}
            </span>
          ) : null}
          <button disabled={loading} onClick={() => void load()} type="button">
            {m.refresh}
          </button>
        </div>
      </div>

      <p className="muted">{m.intro}</p>

      {error ? <p className="error">{error}</p> : null}
      {message ? <p className="hint">{message}</p> : null}

      {report === null ? (
        <p className="muted">{loading ? m.checking : m.noHealthData}</p>
      ) : (
        <ul className="health-list">
          {report.checks.map((check) => (
            <li className={`health-row health-row--${check.status}`} key={check.id}>
              <div className="health-row-head">
                <span className={`health-pill health-pill--${check.status}`}>
                  {m.statusLabel[check.status]}
                </span>
                <strong>{check.label}</strong>
                <span className="muted">{formatLatency(check.latency_ms)}</span>
                {check.heal ? (
                  <button
                    disabled={busy !== ""}
                    onClick={() => runHeal(check.heal as string)}
                    type="button"
                  >
                    {busy === check.heal
                      ? m.working
                      : (m.healLabel[check.heal] ?? check.heal)}
                  </button>
                ) : null}
              </div>
              <p className="health-detail">{check.detail}</p>
              {check.items.length > 0 ? (
                <ul className="health-items">
                  {check.items.map((item) => (
                    <li key={item}>
                      <code>{item}</code>
                    </li>
                  ))}
                </ul>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

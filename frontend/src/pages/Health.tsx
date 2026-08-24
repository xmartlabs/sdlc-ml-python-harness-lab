import { useCallback, useEffect, useState } from "react";

import { api } from "../api";
import type { HealthCheck, HealthReport } from "../types";

const REFRESH_INTERVAL_MS = 30000;

const STATUS_LABEL: Record<HealthCheck["status"], string> = {
  ok: "OK",
  warn: "Attention",
  error: "Failing",
  unknown: "Unknown"
};

const HEAL_LABEL: Record<string, string> = {
  skills_sync: "Re-sync skill views",
  materialize_staged_skills: "Rebuild staging tree",
  refresh_models: "Refresh model catalog",
  reconcile_runs: "Reconcile stuck runs",
  build_runner_image: "Build runner image"
};

function formatLatency(ms: number | null): string {
  if (ms === null) {
    return "";
  }
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
}

export function Health() {
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
        <h2>System health</h2>
        <div className="panel-actions">
          {report ? (
            <span className={`health-pill health-pill--${report.status}`}>
              {STATUS_LABEL[report.status]}
            </span>
          ) : null}
          <button disabled={loading} onClick={() => void load()} type="button">
            Refresh
          </button>
        </div>
      </div>

      <p className="muted">
        What the lab can reach and where its generated state has drifted. Refreshes
        every 30 seconds. Repairs are a fixed set of actions — there is no generic
        command runner here.
      </p>

      {error ? <p className="error">{error}</p> : null}
      {message ? <p className="hint">{message}</p> : null}

      {report === null ? (
        <p className="muted">{loading ? "Checking…" : "No health data."}</p>
      ) : (
        <ul className="health-list">
          {report.checks.map((check) => (
            <li className={`health-row health-row--${check.status}`} key={check.id}>
              <div className="health-row-head">
                <span className={`health-pill health-pill--${check.status}`}>
                  {STATUS_LABEL[check.status]}
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
                      ? "Working…"
                      : (HEAL_LABEL[check.heal] ?? check.heal)}
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

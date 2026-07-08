import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { ErrorBanner } from "../components/ErrorBanner";
import { SandboxPanel } from "../components/SandboxPanel";
import { StateBadge } from "../components/StateBadge";
import type {
  AttributionRecord,
  AuditRecord,
  RunEventRecord,
  RunRecord,
  ValidationRecord
} from "../types";

type RunDetailProps = {
  run: RunRecord | null;
  onCancelRun: (runId: string) => Promise<RunRecord>;
  onRetryRun: (runId: string) => Promise<RunRecord>;
  onScoreRun: (runId: string, score: number, interventions: number, notes: string) => Promise<RunRecord>;
};

const POLL_INTERVAL_MS = 1500;
export const CANCELLABLE_STATES: RunRecord["state"][] = ["pending", "running"];
export const RETRYABLE_STATES: RunRecord["state"][] = ["failed", "cancelled"];

export function RunDetail({ run, onCancelRun, onRetryRun, onScoreRun }: RunDetailProps) {
  const [events, setEvents] = useState<RunEventRecord[]>([]);
  const [actionPending, setActionPending] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [scoreValue, setScoreValue] = useState(0);
  const [interventionsValue, setInterventionsValue] = useState(0);
  const [notesValue, setNotesValue] = useState("");
  const [validation, setValidation] = useState<ValidationRecord | null>(null);
  const [attribution, setAttribution] = useState<AttributionRecord | null>(null);
  const [audit, setAudit] = useState<AuditRecord | null>(null);
  const [auditModel, setAuditModel] = useState("");
  const [tab, setTab] = useState<"detail" | "terminal" | "desktop">("detail");
  const consoleRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    setEvents([]);
    setActionError(null);

    if (run === null) {
      return;
    }

    let active = true;

    const fetchLogs = () => {
      api
        .listRunLogs(run.run_id)
        .then((fetched) => {
          if (active) {
            setEvents(fetched);
          }
        })
        .catch(() => {
          // Log fetching is best-effort; keep showing the last known events.
        });
    };

    fetchLogs();

    if (run.state !== "running") {
      return () => {
        active = false;
      };
    }

    const interval = window.setInterval(fetchLogs, POLL_INTERVAL_MS);
    return () => {
      active = false;
      window.clearInterval(interval);
    };
  }, [run?.run_id, run?.state]);

  useEffect(() => {
    if (consoleRef.current) {
      consoleRef.current.scrollTop = consoleRef.current.scrollHeight;
    }
  }, [events]);

  useEffect(() => {
    if (run === null) {
      return;
    }
    setScoreValue(run.score ?? 0);
    setInterventionsValue(run.interventions ?? 0);
    setNotesValue(run.notes ?? "");
  }, [run?.run_id, run?.score, run?.interventions, run?.notes]);

  useEffect(() => {
    setValidation(null);
    setAttribution(null);
    setAudit(null);
    if (run === null) {
      return;
    }
    let active = true;
    api
      .getValidation(run.run_id)
      .then((result) => {
        if (active) {
          setValidation(result);
        }
      })
      .catch(() => {
        // No validation yet (404) is expected until a run finishes.
      });
    api
      .getAttribution(run.run_id)
      .then((result) => {
        if (active) {
          setAttribution(result);
        }
      })
      .catch(() => {
        // No attribution yet (404) is expected until a run finishes.
      });
    api
      .getAudit(run.run_id)
      .then((result) => {
        if (active) {
          setAudit(result);
        }
      })
      .catch(() => {
        // No audit yet (404) is expected until one is requested.
      });
    return () => {
      active = false;
    };
  }, [run?.run_id, run?.state]);

  if (run === null) {
    return (
      <section className="run-detail-page">
        <Link className="run-detail-back" to="/runs">
          ← Runs
        </Link>
        <p className="muted">Select a run to view status, artifacts, score, and audit.</p>
      </section>
    );
  }

  const canCancel = CANCELLABLE_STATES.includes(run.state);
  const canRetry = RETRYABLE_STATES.includes(run.state);

  const handleCancel = () => {
    setActionPending(true);
    setActionError(null);
    onCancelRun(run.run_id)
      .catch((error: Error) => setActionError(error.message))
      .finally(() => setActionPending(false));
  };

  const handleRetry = () => {
    setActionPending(true);
    setActionError(null);
    onRetryRun(run.run_id)
      .catch((error: Error) => setActionError(error.message))
      .finally(() => setActionPending(false));
  };

  const handleScore = () => {
    const clampedScore = Math.max(0, Math.min(10, scoreValue));
    const normalizedInterventions = Math.max(0, interventionsValue);
    setActionPending(true);
    setActionError(null);
    onScoreRun(run.run_id, clampedScore, normalizedInterventions, notesValue)
      .then(() => api.getValidation(run.run_id).then(setValidation).catch(() => {}))
      .catch((error: Error) => setActionError(error.message))
      .finally(() => setActionPending(false));
  };

  const handleRevalidate = () => {
    const runId = run.run_id;
    setActionPending(true);
    setActionError(null);
    api
      .validateRun(runId)
      .then(() => {
        // Validation runs asynchronously in the worker; poll for the result.
        let tries = 0;
        const poll = () => {
          api
            .getValidation(runId)
            .then((result) => setValidation(result))
            .catch(() => {});
          tries += 1;
          if (tries < 8) {
            window.setTimeout(poll, 3000);
          }
        };
        window.setTimeout(poll, 3000);
      })
      .catch((error: Error) => setActionError(error.message))
      .finally(() => setActionPending(false));
  };

  const objectiveBadge = (passed: boolean | null | undefined) => {
    if (passed === true) return <span className="badge ok">objective: pass</span>;
    if (passed === false) return <span className="badge error">objective: fail</span>;
    return <span className="badge">objective: manual</span>;
  };

  const handleIndexAttribution = () => {
    const runId = run.run_id;
    setActionPending(true);
    setActionError(null);
    api
      .indexAttribution(runId)
      .then((result) => setAttribution(result))
      .catch((error: Error) => setActionError(error.message))
      .finally(() => setActionPending(false));
  };

  const handleRunAudit = () => {
    const runId = run.run_id;
    const model = auditModel.trim() || undefined;
    setActionPending(true);
    setActionError(null);
    setAudit({
      run_id: runId,
      auditor_model: model ?? "",
      status: "running",
      markdown: "",
      error: null,
      created_at: "",
      updated_at: ""
    });
    api
      .runAudit(runId, model)
      .then(() => {
        // The audit is an LLM call in the worker; poll until it settles.
        let tries = 0;
        const poll = () => {
          api
            .getAudit(runId)
            .then((result) => {
              setAudit(result);
              if (result.status === "running" && tries < 40) {
                tries += 1;
                window.setTimeout(poll, 3000);
              }
            })
            .catch(() => {
              if (tries < 40) {
                tries += 1;
                window.setTimeout(poll, 3000);
              }
            });
        };
        window.setTimeout(poll, 3000);
      })
      .catch((error: Error) => setActionError(error.message))
      .finally(() => setActionPending(false));
  };

  const countEntries = (counts: Record<string, number>) =>
    Object.entries(counts)
      .sort((a, b) => b[1] - a[1])
      .map(([name, count]) => `${name}×${count}`)
      .join("  ");

  return (
    <section className="run-detail-page">
      <Link className="run-detail-back" to="/runs">
        ← Runs
      </Link>
      <div className="run-detail-header">
        <p className="eyebrow">Run detail</p>
        <h2>{run.run_id}</h2>
        <StateBadge state={run.state} />
        <div className="run-actions">
          <button disabled={!canCancel || actionPending} onClick={handleCancel} type="button">
            Cancel
          </button>
          <button disabled={!canRetry || actionPending} onClick={handleRetry} type="button">
            Retry
          </button>
        </div>
        {actionError !== null ? <ErrorBanner message={actionError} onDismiss={() => setActionError(null)} /> : null}
      </div>
      <div className="run-tabs">
        {(["detail", "terminal", "desktop"] as const).map((name) => (
          <button
            className={tab === name ? "run-tab active" : "run-tab"}
            key={name}
            onClick={() => setTab(name)}
            type="button"
          >
            {name === "detail" ? "Detail" : name === "terminal" ? "Terminal" : "Desktop"}
          </button>
        ))}
      </div>
      {tab !== "detail" ? <SandboxPanel key={tab} kind={tab} runId={run.run_id} /> : null}
      {tab === "detail" ? (
        <>
      <div className="detail-grid">
        <div>
          <span>Score</span>
          <strong>{run.score ?? "—"}</strong>
        </div>
        <div>
          <span>Interventions</span>
          <strong>{run.interventions ?? "—"}</strong>
        </div>
        <div>
          <span>Exit code</span>
          <strong>{run.exit_code ?? "—"}</strong>
        </div>
        <div>
          <span>Started</span>
          <strong>{run.started_at ?? "—"}</strong>
        </div>
        <div>
          <span>Ended</span>
          <strong>{run.ended_at ?? "—"}</strong>
        </div>
        <div>
          <span>Failure</span>
          <strong>{run.failure_reason ?? "None"}</strong>
        </div>
      </div>
      {run.state === "awaiting_manual_score" ? (
        <div className="panel subtle">
          <div className="panel-head">
            <h3>Manual score</h3>
            <span className="muted">Required to complete run</span>
          </div>
          <div className="form-grid score-form-grid">
            <label>
              <span>Score (0-10)</span>
              <input
                max={10}
                min={0}
                onChange={(event) => setScoreValue(Number(event.target.value))}
                type="number"
                value={scoreValue}
              />
            </label>
            <label>
              <span>Interventions</span>
              <input
                min={0}
                onChange={(event) => setInterventionsValue(Number(event.target.value))}
                type="number"
                value={interventionsValue}
              />
            </label>
            <label className="full">
              <span>Notes</span>
              <textarea onChange={(event) => setNotesValue(event.target.value)} value={notesValue} />
            </label>
          </div>
          <div className="panel-actions score-panel-actions">
            <button className="primary" disabled={actionPending} onClick={handleScore} type="button">
              {actionPending ? "Saving..." : "Submit score"}
            </button>
          </div>
        </div>
      ) : null}
      <div className="run-detail-grid">
      <div className="panel subtle">
        <div className="panel-head">
          <h3>Validation & score</h3>
          <div className="run-actions">
            {validation ? objectiveBadge(validation.objective?.passed) : null}
            <button disabled={actionPending} onClick={handleRevalidate} type="button">
              Re-validate
            </button>
          </div>
        </div>
        {validation === null ? (
          <p className="muted">
            No validation yet. Objective validation runs automatically when a run
            finishes; use Re-validate to run it now.
          </p>
        ) : (
          <>
            <div className="detail-grid">
              <div>
                <span>Composite</span>
                <strong>{validation.composite ?? "—"}</strong>
              </div>
              <div>
                <span>Functional</span>
                <strong>{validation.subjective?.functional_score ?? "—"}</strong>
              </div>
              <div>
                <span>Objective</span>
                <strong>{validation.objective?.summary ?? "—"}</strong>
              </div>
              <div>
                <span>Tokens</span>
                <strong>{validation.cost?.total_tokens ?? "—"}</strong>
              </div>
              <div>
                <span>Cost (USD)</span>
                <strong>{validation.cost?.cost_usd ?? "—"}</strong>
              </div>
              <div>
                <span>Steps</span>
                <strong>{validation.effort?.steps ?? "—"}</strong>
              </div>
              <div>
                <span>Interventions</span>
                <strong>{validation.effort?.interventions ?? "—"}</strong>
              </div>
              <div>
                <span>Time (min)</span>
                <strong>{validation.cost?.time_minutes ?? "—"}</strong>
              </div>
            </div>
            {validation.objective?.checks && validation.objective.checks.length > 0 ? (
              <div className="run-console">
                {validation.objective.checks.map((check, index) => (
                  <div
                    className={`run-console-line ${check.passed ? "info" : "error"}`}
                    key={index}
                  >
                    <span>{check.passed ? "✓" : "✗"}</span>
                    <span>
                      {check.name} (exit {check.exit_code ?? "?"}) {check.detail}
                    </span>
                  </div>
                ))}
              </div>
            ) : null}
          </>
        )}
        <p className="muted">
          Objective validation runs a headless smoke of workspace/{run.run_id}/ in the
          runner container (SDL dummy driver). The functional level below is the
          subjective dimension; the composite gates it by the objective floor.
        </p>
      </div>
      <div className="panel subtle">
        <div className="panel-head">
          <h3>Harness attribution</h3>
          <div className="run-actions">
            {attribution ? (
              <span className="badge">
                governance {attribution.harness_usage?.governance_coverage ?? "—"}
              </span>
            ) : null}
            <button disabled={actionPending} onClick={handleIndexAttribution} type="button">
              Re-index
            </button>
          </div>
        </div>
        {attribution === null ? (
          <p className="muted">
            No attribution yet. It is computed automatically after a run from the
            opencode step logs; use Re-index to compute it now.
          </p>
        ) : (
          <>
            <div className="detail-grid">
              <div>
                <span>Tools</span>
                <strong>{countEntries(attribution.tools) || "—"}</strong>
              </div>
              <div>
                <span>Agent modes</span>
                <strong>{countEntries(attribution.agent_modes) || "—"}</strong>
              </div>
              <div>
                <span>Governance read</span>
                <strong>{attribution.harness_usage?.governance_coverage ?? "—"}</strong>
              </div>
              <div>
                <span>Skills consulted</span>
                <strong>
                  {(attribution.harness_usage?.skills_consulted?.length ?? 0)}/
                  {attribution.harness_usage?.skills_available?.length ?? 0}
                </strong>
              </div>
              <div>
                <span>Bash</span>
                <strong>{countEntries(attribution.bash_commands) || "—"}</strong>
              </div>
              <div>
                <span>Steps parsed</span>
                <strong>{attribution.steps_parsed}</strong>
              </div>
            </div>
            {attribution.harness_usage?.governance_docs_read &&
            attribution.harness_usage.governance_docs_read.length > 0 ? (
              <p className="muted">
                Governance read: {attribution.harness_usage.governance_docs_read.join(", ")}
              </p>
            ) : (
              <p className="muted">No governance docs were read in this run.</p>
            )}
            <p className="muted">{attribution.harness_usage?.skills_note}</p>
          </>
        )}
      </div>
      <div className="panel subtle">
        <div className="panel-head">
          <h3>LLM audit</h3>
          <div className="run-actions">
            {audit ? <span className="badge">{audit.status}</span> : null}
            <input
              className="audit-model-input"
              onChange={(event) => setAuditModel(event.target.value)}
              placeholder="auditor model (optional)"
              type="text"
              value={auditModel}
            />
            <button
              disabled={actionPending || audit?.status === "running"}
              onClick={handleRunAudit}
              type="button"
            >
              {audit?.status === "running" ? "Auditing..." : "Run audit"}
            </button>
          </div>
        </div>
        {audit === null ? (
          <p className="muted">
            No audit yet. A strong LLM reads this run's logs, diff, usage and score
            and writes a qualitative diagnostic (skill/rule usage, efficiency, SDLC
            adherence, failure points). Runs in the worker; this may take a minute.
          </p>
        ) : audit.status === "running" ? (
          <p className="muted">Auditing with {audit.auditor_model || "default model"}…</p>
        ) : audit.status === "failed" ? (
          <ErrorBanner message={audit.error ?? "Audit failed"} />
        ) : (
          <div className="audit-body">{audit.markdown}</div>
        )}
      </div>
      <div>
        <p className="sidebar-label">Command preview</p>
        <code className="command-preview">{run.command.join(" ")}</code>
      </div>
      <div className="panel subtle">
        <div className="panel-head">
          <h3>Logs</h3>
          <span className="muted">{run.state === "running" ? "live" : "history"}</span>
        </div>
        <div className="run-console" ref={consoleRef}>
          {events.length === 0 ? (
            <p className="muted">No log events yet.</p>
          ) : (
            events.map((event) => (
              <div className={`run-console-line ${event.level}`} key={event.id}>
                <span className="run-console-time">{event.created_at}</span>
                <span>{event.message}</span>
              </div>
            ))
          )}
        </div>
      </div>
      <div className="panel subtle">
        <div className="panel-head">
          <h3>Artifacts</h3>
          <span className="muted">data/runs/{run.run_id}</span>
        </div>
        <p className="muted">metadata.json, prompt.md, report.md, audit.md, diff.patch</p>
      </div>
      </div>
        </>
      ) : null}
    </section>
  );
}

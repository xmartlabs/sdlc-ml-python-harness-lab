import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { RunEventRecord, RunRecord } from "../types";

type RunDetailProps = {
  run: RunRecord | null;
  onCancelRun: (runId: string) => Promise<RunRecord>;
  onRetryRun: (runId: string) => Promise<RunRecord>;
  onScoreRun: (runId: string, score: number, interventions: number, notes: string) => Promise<RunRecord>;
};

const POLL_INTERVAL_MS = 1500;
const CANCELLABLE_STATES: RunRecord["state"][] = ["pending", "running"];
const RETRYABLE_STATES: RunRecord["state"][] = ["failed", "cancelled"];

export function RunDetail({ run, onCancelRun, onRetryRun, onScoreRun }: RunDetailProps) {
  const [events, setEvents] = useState<RunEventRecord[]>([]);
  const [actionPending, setActionPending] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [scoreValue, setScoreValue] = useState(0);
  const [interventionsValue, setInterventionsValue] = useState(0);
  const [notesValue, setNotesValue] = useState("");
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

  if (run === null) {
    return (
      <section className="inspector-stack">
        <h2>Inspector</h2>
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
      .catch((error: Error) => setActionError(error.message))
      .finally(() => setActionPending(false));
  };

  return (
    <section className="inspector-stack">
      <div>
        <p className="eyebrow">Run detail</p>
        <h2>{run.run_id}</h2>
        <p className="muted">{run.state}</p>
        <div className="run-actions">
          <button disabled={!canCancel || actionPending} onClick={handleCancel} type="button">
            Cancel
          </button>
          <button disabled={!canRetry || actionPending} onClick={handleRetry} type="button">
            Retry
          </button>
        </div>
        {actionError !== null ? <p className="muted error">{actionError}</p> : null}
      </div>
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
      <div className="panel subtle">
        <div className="panel-head">
          <h3>Play-test note</h3>
          <span className="muted">Tetris GUI</span>
        </div>
        <p className="muted">
          Run play-tests from the host on workspace/{run.run_id}/ (example: python workspace/{run.run_id}/main.py). The
          worker container has no display, so pygame cannot be validated inside Docker.
        </p>
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
    </section>
  );
}
